"""Pure bounded JSON interpreter. No credentials, imports, eval, filesystem or I/O operators."""
import json
import re
import sys
from urllib.parse import quote, urlencode

LIMIT=2*1024*1024
MISSING=object()


class MappingError(ValueError):
    pass


def bounded(value):
    stack,count=[(value,0)],0
    while stack:
        node,depth=stack.pop();count+=1
        if depth>32 or count>10000: raise MappingError('mapping_limit')
        if isinstance(node,dict):
            if any(not isinstance(key,str) for key in node): raise MappingError('mapping_type')
            stack.extend((item,depth+1) for item in node.values())
        elif isinstance(node,list): stack.extend((item,depth+1) for item in node)
        elif not isinstance(node,(str,int,float,bool,type(None))): raise MappingError('mapping_type')
    if len(json.dumps(value,allow_nan=False).encode())>LIMIT: raise MappingError('mapping_limit')
    return value


def select(value,path):
    if not isinstance(path,str) or len(path)>1024 or path and not path.startswith('/') or len(path.split('/'))>17:
        raise MappingError('invalid_selector')
    if not path: return value
    for part in path[1:].split('/'):
        part=part.replace('~1','/').replace('~0','~')
        if isinstance(value,dict): value=value.get(part,MISSING)
        elif isinstance(value,list) and re.fullmatch(r'0|[1-9][0-9]{0,5}',part):
            index=int(part);value=value[index] if index<len(value) else MISSING
        else: return MISSING
    return value


def expression(node,context,budget=None,depth=0):
    budget=budget if budget is not None else [10000]
    budget[0]-=1
    if depth>32 or budget[0]<0: raise MappingError('mapping_limit')
    if isinstance(node,list): return [expression(item,context,budget,depth+1) for item in node]
    if not isinstance(node,dict): return node
    operators={'input','native','literal','default','each','next_page'}
    reserved={key[1:] for key in node if key.startswith("$")}
    if reserved:
        if len(node)!=1 or len(reserved)!=1 or not reserved<=operators: raise MappingError('invalid_expression')
        op=next(iter(reserved));value=node["$"+op]
        if op in {'input','native'}: return select(context.get(op,{}),value)
        if op=='literal': return value
        if op=='next_page':
            if value is not True: raise MappingError('invalid_expression')
            return context.get('next_page')
        if op=='default':
            if not isinstance(value,list) or len(value)!=2: raise MappingError('invalid_expression')
            selected=expression(value[0],context,budget,depth+1)
            return expression(value[1],context,budget,depth+1) if selected is MISSING else selected
        if op=='each':
            if not isinstance(value,dict) or set(value)-{'items','map','exclude_if_has'} or not {'items','map'}<=set(value): raise MappingError('invalid_expression')
            items=expression(value['items'],context,budget,depth+1)
            if not isinstance(items,list) or len(items)>1000: raise MappingError('mapping_type')
            field=value.get('exclude_if_has')
            if field is not None and (not isinstance(field,str) or not field): raise MappingError('invalid_expression')
            return [expression(value['map'],{**context,'native':item},budget,depth+1) for item in items if not (field is not None and isinstance(item,dict) and field in item)]
    return {key:expression(value,context,budget,depth+1) for key,value in node.items()}


def evaluate(payload):
    bounded(payload)
    if set(payload)-{'mode','mapping','input','native','next_page'} or payload.get('mode') not in {'request','output'}:
        raise MappingError('invalid_task')
    mapping=payload['mapping'];context={key:payload.get(key) for key in ('input','native','next_page')}
    if payload['mode']=='output': return bounded(expression(mapping['output_map'],context))
    request=mapping['request']
    if not isinstance(request,dict) or set(request)-{'base_url','method','path_template','query','json'} or not {'base_url','method','path_template'}<=set(request): raise MappingError('invalid_request')
    if request['method'] not in {'GET','POST'} or not isinstance(request['base_url'],str) or not isinstance(request['path_template'],str): raise MappingError('invalid_request')
    path=request['path_template']
    if len(path)>4096 or not path.startswith('/') or path.startswith('//'): raise MappingError('invalid_request')
    def replace(match):
        value=select(context['input'],'/'+match.group(1))
        if not isinstance(value,(str,int)) or isinstance(value,bool): raise MappingError('mapping_type')
        return quote(str(value),safe='')
    path=re.sub(r'\{([a-z][a-z0-9_]*)\}',replace,path)
    if any(character in path for character in '{}?#\\') or any(ord(c)<33 for c in path): raise MappingError('invalid_request')
    query=expression(request.get('query',{}),context)
    if not isinstance(query,dict) or any(value is MISSING or not isinstance(value,(str,int,bool,type(None))) for value in query.values()): raise MappingError('mapping_type')
    url=request['base_url'].rstrip('/')+path
    if query: url+='?'+urlencode({key:value for key,value in query.items() if value is not None})
    body=expression(request.get('json'),context)
    if request['method']=='GET' and body is not None: raise MappingError('invalid_request')
    return bounded({'method':request['method'],'url':url,'json':body})


def main():
    try:
        raw=sys.stdin.buffer.read(LIMIT+1)
        if len(raw)>LIMIT: raise MappingError('mapping_limit')
        value=json.loads(raw,parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        result={'status':'completed','result':evaluate(value)}
    except Exception:
        result={'status':'failed','category':'unsupported_capability'}
    sys.stdout.write(json.dumps(result,allow_nan=False))


if __name__=='__main__': main()
