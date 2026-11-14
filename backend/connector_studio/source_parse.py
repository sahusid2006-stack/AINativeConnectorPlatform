"""Deterministic proposals, never executable instructions or invented source facts."""
import json
import math
import re
from html.parser import HTMLParser

import yaml
from jsonschema import Draft202012Validator, SchemaError
from yaml.events import AliasEvent, CollectionStartEvent, CollectionEndEvent

from .access import AccessError
from .contracts import offline_validator
from .source_fetch import MAX_BYTES, origin


def bounded(value):
    stack, count = [(value, 0)], 0
    while stack:
        item, depth = stack.pop(); count += 1
        if depth > 32 or count > 10000: raise AccessError("source_parser_limit", 422)
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item): raise AccessError("source_invalid_document", 422)
            stack.extend((child, depth+1) for child in item.values())
        elif isinstance(item, list): stack.extend((child, depth+1) for child in item)
        elif not isinstance(item, (str, int, float, bool, type(None))) or isinstance(item, float) and not math.isfinite(item):
            raise AccessError("source_invalid_document", 422)
    return value


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result: raise AccessError("source_duplicate_key", 422)
        result[key] = value
    return result


class StrictLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        if any(key.tag != "tag:yaml.org,2002:str" for key, _ in node.value):
            raise AccessError("source_invalid_document", 422)
        return unique_pairs([(self.construct_object(key, deep=deep), self.construct_object(value, deep=deep)) for key,value in node.value])


class VisibleHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False); self.parts=[]; self.depth=0; self.nodes=0; self.hidden=0
    def handle_starttag(self, tag, attrs):
        self.nodes += 1
        if tag not in {"br","hr","img","input","meta","link","area","base","embed","param","source","track","wbr"}: self.depth += 1
        if tag in {"script","style","template"}: self.hidden += 1
        if self.depth > 32 or self.nodes > 10000: raise AccessError("source_parser_limit", 422)
        if tag in {"br","p","div","pre","li","h1","h2","h3"}: self.parts.append("\n")
    def handle_endtag(self, tag):
        self.depth=max(0,self.depth-1)
        if tag in {"script","style","template"}: self.hidden=max(0,self.hidden-1)
        self.parts.append("\n")
    def handle_data(self, data):
        self.nodes += 1
        if self.nodes > 10000: raise AccessError("source_parser_limit", 422)
        if not self.hidden: self.parts.append(data)


def parse_document(content, mime_type):
    if len(content) > MAX_BYTES: raise AccessError("source_byte_limit", 413)
    try: text = content.decode("utf-8")
    except UnicodeError: raise AccessError("source_utf8_required", 422) from None
    if mime_type in {"application/json", "application/vnd.oai.openapi+json"}:
        try:
            value = json.loads(text, object_pairs_hook=unique_pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            bounded(value)
        except (ValueError, RecursionError): raise AccessError("source_invalid_document", 422) from None
        return text, value
    if mime_type in {"application/yaml","application/x-yaml","text/yaml","application/vnd.oai.openapi"}:
        try:
            depth, count = 0, 0
            for event in yaml.parse(text):
                count += 1
                if isinstance(event, AliasEvent) or getattr(event,"anchor",None): raise AccessError("source_yaml_alias_denied", 422)
                if isinstance(event, CollectionStartEvent): depth += 1
                if isinstance(event, CollectionEndEvent): depth -= 1
                if depth > 32 or count > 20000: raise AccessError("source_parser_limit", 422)
            value = bounded(yaml.load(text, Loader=StrictLoader))
        except (yaml.YAMLError, RecursionError): raise AccessError("source_invalid_document", 422) from None
        return text, value
    if mime_type == "text/html":
        parser=VisibleHTML(); parser.feed(text); parser.close()
        return text, "".join(parser.parts)
    if mime_type in {"text/plain","text/markdown"}: return text, text
    raise AccessError("source_format_unsupported", 422)


def span(text, needle, pointer=None):
    start = text.find(needle)
    if start < 0: start=0
    end = min(len(text), start+512)
    result = {"start_line":text.count("\n",0,start)+1,"end_line":text.count("\n",0,end)+1,"quote":text[start:end]}
    if pointer: result["pointer"] = pointer
    return result


def unresolved(fact, operation=None):
    return {"fact":fact,"operation_id":operation,"input_needed":f"Provide explicit {fact} documentation" + (f" for {operation}." if operation else ".")}



def materialize(value, document, budget, trail=(), depth=0):
    budget[0] -= 1
    if depth > 32 or budget[0] < 0: raise AccessError("source_parser_limit",422)
    if isinstance(value,str):
        budget[1] -= len(value.encode())
        if budget[1] < 0: raise AccessError("source_parser_limit",422)
    if isinstance(value,dict):
        if "$ref" in value:
            reference=value["$ref"]
            if not isinstance(reference,str) or not reference.startswith("#/") or reference in trail:
                raise ValueError("Unsupported external or recursive reference")
            target=document
            try:
                for part in reference[2:].split("/"):
                    part=part.replace("~1","/").replace("~0","~")
                    target=target[int(part)] if isinstance(target,list) else target[part]
            except (KeyError,IndexError,TypeError,ValueError): raise ValueError("Unresolved local reference") from None
            if len(value)!=1: raise ValueError("Reference siblings require explicit inline facts")
            return materialize(target,document,budget,trail+(reference,),depth+1)
        result={}
        for key,child in value.items():
            budget[1] -= len(key.encode())
            if budget[1] < 0: raise AccessError("source_parser_limit",422)
            result[key]=materialize(child,document,budget,trail,depth+1)
        return result
    if isinstance(value,list): return [materialize(child,document,budget,trail,depth+1) for child in value]
    return value

def proposals(content, mime_type):
    text, document = parse_document(content, mime_type)
    result=[]
    if isinstance(document, dict):
        if not str(document.get("openapi", "")).startswith("3.0."):
            return [], [unresolved("OpenAPI 3.0 document (3.1/Swagger require explicit supported schemas)")]
        paths = document.get("paths")
        if not isinstance(paths, dict): return [], [unresolved("operation paths")]
        budget=[10000,MAX_BYTES]
        for path, item in paths.items():
            if not isinstance(item, dict) or not path.startswith("/"): continue
            for method, operation in item.items():
                if method not in {"get","post","put","patch","delete","head","options"}: continue
                if not isinstance(operation, dict): continue
                if len(path)>4096: raise AccessError("source_parser_limit",422)
                name = operation.get("operationId")
                name = name if isinstance(name,str) and 0 < len(name) <=128 else f"{method.upper()} {path}"
                facts={"method":method.upper(),"path":path,"base_url":None,"input_schema":None,"output_schema":None,"authentication":None}
                missing=[]
                servers=operation.get("servers", item.get("servers",document.get("servers")))
                if isinstance(servers,list) and len(servers)==1 and isinstance(servers[0],dict) and isinstance(servers[0].get("url"),str) and "{" not in servers[0]["url"]:
                    try:
                        origin(servers[0]["url"])
                        facts["base_url"]=servers[0]["url"]
                    except AccessError: missing.append(unresolved("an absolute server URL without embedded credentials",name))
                else: missing.append(unresolved("one unambiguous absolute server URL",name))
                try:
                    operation=materialize(operation,document,budget)
                    inherited=materialize(item.get("parameters",[]),document,budget)
                    own=operation.get("parameters",[])
                    if not isinstance(inherited,list) or not isinstance(own,list): raise ValueError()
                    inherited_keys=[(entry.get("in"),entry.get("name")) for entry in inherited if isinstance(entry,dict)]
                    own_keys=[(entry.get("in"),entry.get("name")) for entry in own if isinstance(entry,dict)]
                    if len(set(inherited_keys))!=len(inherited_keys) or len(set(own_keys))!=len(own_keys): raise ValueError()
                    parameters=[entry for entry in inherited if not isinstance(entry,dict) or (entry.get("in"),entry.get("name")) not in own_keys]+own
                except (ValueError,TypeError):
                    parameters=None
                    missing.append(unresolved("nonrecursive local references and unambiguous parameter definitions",name))
                    operation={}

                properties, required={},[]
                valid=isinstance(parameters,list)
                for parameter in parameters if valid else []:
                    if not isinstance(parameter,dict) or parameter.get("in") not in {"path","query"} or not isinstance(parameter.get("name"),str) or not isinstance(parameter.get("schema"),dict): valid=False; continue
                    key=parameter["name"]
                    if key in properties: valid=False
                    properties[key]=parameter["schema"]
                    if parameter.get("required") is True: required.append(key)
                    elif parameter["in"]=="path": valid=False
                body=operation.get("requestBody")
                if body is not None:
                    if isinstance(body,dict) and isinstance(body.get("content"),dict):
                        schema=body["content"].get("application/json",{}).get("schema") if isinstance(body["content"].get("application/json",{}),dict) else None
                        if isinstance(schema,dict) and "body" not in properties:
                            properties["body"]=schema
                            if body.get("required") is True: required.append("body")
                        else: valid=False
                    else: valid=False
                if set(re.findall(r"\{([^{}]+)\}",path)) - set(properties): valid=False
                if valid: facts["input_schema"]={"type":"object","properties":properties,"required":required,"additionalProperties":False}
                else: missing.append(unresolved("complete path/query/body input schemas",name))
                responses=operation.get("responses",{})
                schemas=[]
                if isinstance(responses,dict):
                    for status, response in responses.items():
                        if re.fullmatch(r"2[0-9][0-9]",str(status)) and isinstance(response,dict):
                            contents=response.get("content",{})
                            media=contents.get("application/json",{}) if isinstance(contents,dict) else {}
                            schema=media.get("schema") if isinstance(media,dict) else None
                            if isinstance(schema,dict): schemas.append(schema)
                if schemas and all(schema==schemas[0] for schema in schemas): facts["output_schema"]=schemas[0]
                else: missing.append(unresolved("one unambiguous success response JSON schema",name))
                security=operation.get("security",document.get("security"))
                components=document.get("components",{})
                schemes=components.get("securitySchemes",{}) if isinstance(components,dict) else {}
                if security == []: facts["authentication"]={"type":"none"}
                elif isinstance(security,list) and len(security)==1 and isinstance(security[0],dict) and len(security[0])==1 and isinstance(schemes,dict):
                    auth_name, scopes=next(iter(security[0].items())); definition=schemes.get(auth_name,{})
                    try: definition=materialize(definition,document,budget)
                    except ValueError: definition={}
                    if isinstance(definition,dict) and definition.get("type")=="http" and definition.get("scheme") in {"bearer","basic"} and scopes==[]:
                        facts["authentication"]={"type":definition["scheme"],"credential_reference_required":True}
                    elif isinstance(definition,dict) and definition.get("type")=="apiKey" and definition.get("in") in {"header","query"} and isinstance(definition.get("name"),str) and scopes==[]:
                        facts["authentication"]={"type":"apiKey","in":definition["in"],"name":definition["name"],"credential_reference_required":True}
                if facts["authentication"] is None: missing.append(unresolved("explicit authentication scheme and scopes (or security: [])",name))
                for field in ("input_schema","output_schema"):
                    if facts[field] is not None:
                        try:
                            offline_validator(facts[field])
                            Draft202012Validator.check_schema(facts[field])
                        except (ValueError, SchemaError):
                            facts[field]=None; missing.append(unresolved(f"inline offline {field}; external references are unsupported",name))
                pointer="/paths/"+path.replace("~","~0").replace("/","~1")+"/"+method
                result.append({"name":name,"facts":facts,"classification":"read" if method.lower() in {"get","head","options"} else "write","evidence":[span(text, operation.get("operationId",path) if isinstance(operation.get("operationId",path),str) else path,pointer)],"unresolved":missing})
    elif isinstance(document,str):
        bases=set(re.findall(r"(?im)^Base URL:\s*(https?://[^\s]+)",document))
        bases={base.rstrip(".") for base in bases}
        auths=set(re.findall(r"(?im)^Authorization:\s*(Bearer|Basic|None)\b",document))
        for match in re.finditer(r"(?m)^\s*(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+(/[^\s]+)[^\n]*",document):
            method,path=match.group(1),match.group(2).split("?")[0]; name=f"{method} {path}"
            facts={"method":method,"path":path,"base_url":next(iter(bases)) if len(bases)==1 else None,"authentication":{"type":next(iter(auths)).lower()} if len(auths)==1 else None,"input_schema":None,"output_schema":None}
            missing=[unresolved("explicit input JSON schema",name),unresolved("explicit output JSON schema",name)]
            if facts["base_url"]:
                try: origin(facts["base_url"])
                except AccessError: facts["base_url"]=None
            if not facts["base_url"]: missing.append(unresolved("one unambiguous base URL",name))
            if not facts["authentication"]: missing.append(unresolved("explicit authentication scheme",name))
            result.append({"name":name,"facts":facts,"classification":"read" if method in {"GET","HEAD","OPTIONS"} else "write","evidence":[span(text,match.group(0).strip())],"unresolved":missing})
    if len(result)>100: raise AccessError("source_operation_limit",422)
    return result, [] if result else [unresolved("documented HTTP operations")]
