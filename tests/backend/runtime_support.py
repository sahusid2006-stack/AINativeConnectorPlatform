"""Handcrafted test mappings only. These are not model-generated acceptance evidence."""
from connector_studio.rest_runtime import public_schema


def test_package(source):
    issue={field:{'$native':'/'+field} for field in ('number','title','body','state','html_url')}
    operations,mappings=[],[]
    for short,input_name,output_name,method,path in (
        ('list_issues','ListInput','ListOutput','GET','/repos/{owner}/{repo}/issues'),
        ('get_issue','GetInput','Issue','GET','/repos/{owner}/{repo}/issues/{issue_number}'),
        ('create_issue','CreateInput','Issue','POST','/repos/{owner}/{repo}/issues')):
        name='github_'+short+'_v1'
        operations.append({'name':name,'description':'Handcrafted runtime test','input_schema':public_schema(input_name),'output_schema':public_schema(output_name),'classification':'write' if method=='POST' else 'read','required_privileges':['issues:write' if method=='POST' else 'issues:read'],'source_evidence':[{'source_id':str(source),'excerpt':'Synthetic test contract'}],'protocol_mapping':'rest','pagination':{} if short=='list_issues' else None,'timeout_seconds':30,'retry_policy':{'max_attempts':3,'safe':method=='GET'},'test_references':[]})
        request={'base_url':'http://fixtures:8000','method':method,'path_template':path}
        if short=='list_issues': request['query']={key:{'$input':'/'+key} for key in ('page','per_page')}
        if method=='POST': request['json']={'title':{'$input':'/title'},'body':{'$default':[{'$input':'/body'},None]}}
        output={'issues':{'$each':{'items':{'$native':''},'exclude_if_has':'pull_request','map':issue}},'next_page':{'$next_page':True}} if short=='list_issues' else issue
        mappings.append({'operation':name,'driver':'rest','request':request,'output_map':output})
    return {'manifest':{'operations':operations},'mappings':mappings,'generated_tests':[],'dependency_lock':{'runtime':'handcrafted-test'},'source_snapshot_ids':[str(source)]}
