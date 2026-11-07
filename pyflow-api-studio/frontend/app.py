import os, json, time
import httpx, streamlit as st
st.set_page_config(page_title='PyFlow API Studio',page_icon='🔀',layout='wide')
st.title('PyFlow API Studio')
base=os.getenv('PYFLOW_API_URL','http://localhost:8000')
token=os.getenv('PYFLOW_API_TOKEN','')
headers={'Authorization':'Bearer '+token} if token else {}
def api(method,path,**kwargs):
    try:
        r=httpx.request(method,base+path,headers=headers,timeout=60,**kwargs);r.raise_for_status();return r.json()
    except Exception as exc: st.error(str(exc));st.stop()
def editor(label,value,key): return st.text_area(label,json.dumps(value,indent=2),height=220,key=key)
def parse(value):
    try: return json.loads(value)
    except ValueError as exc: st.error(str(exc));st.stop()
def choose(kind):
    rows=api('GET','/'+kind)
    if not rows: st.info('Create a '+kind.rstrip('s')+' first.');st.stop()
    return st.selectbox(kind.title(),rows,format_func=lambda r:r.get('name',r['id']))
def show_job(job):
    st.session_state['job_id']=job['id'];st.json(job)
page=st.sidebar.radio('Workspace',['Data Workbench','Custom Python','Connections','Endpoint Registry','Endpoint Blueprint Editor','HTTP Request Tester','JSON Response Explorer','JSON Transformation Builder','Workflow Builder','Workflow Execution','Execution History','Stored Results','Settings'])
if page=='Data Workbench':
    from components.workbench import render_workbench
    render_workbench(api)
elif page=='Custom Python':
    from components.python_editor import render_python
    render_python(api)
elif page=='Connections':
    rows=api('GET','/connections');st.dataframe(rows)
    options=['New']+[r['id'] for r in rows];selected=st.selectbox('Edit connection',options)
    value=next((r for r in rows if r['id']==selected),{'name':'Mock service','base_url':'https://api.example.com','approved_hosts':['api.example.com'],'auth_type':'none','credential_ref':None})
    st.caption('Credentials are server environment references to JSON objects. Never paste secret values here.')
    text=editor('Connection configuration',value,'connection')
    if st.button('Save connection'): st.json(api('POST','/connections',json=parse(text)))
    if selected!='New':
        if st.button('Test connection'): st.json(api('POST',f'/connections/{selected}/test'))
        if st.button('Delete connection'): api('DELETE','/connections/'+selected);st.rerun()
elif page=='Endpoint Registry':
    rows=api('GET','/endpoints');st.dataframe(rows)
    st.download_button('Export blueprints',json.dumps(rows,indent=2),'endpoints.json')
    uploaded=st.file_uploader('Import blueprints',type='json')
    if uploaded and st.button('Import'):
        for row in json.load(uploaded): api('POST','/endpoints',json=row)
        st.rerun()
    if rows:
        row=st.selectbox('Blueprint',rows,format_func=lambda r:r['name'])
        if st.button('Duplicate'): st.json(api('POST',f"/endpoints/{row['id']}/duplicate"))
        if st.button('Delete blueprint'): api('DELETE',f"/endpoints/{row['id']}");st.rerun()
elif page=='Endpoint Blueprint Editor':
    rows=api('GET','/endpoints');selected=st.selectbox('Edit',['New']+[r['id'] for r in rows])
    value=next((r for r in rows if r['id']==selected),{})
    connection=choose('connections')
    st.caption('Authentication reference: '+str(connection.get('credential_ref') or connection.get('auth_type','none')))
    name=st.text_input('Endpoint name',value.get('name','New endpoint'))
    methods=['GET','POST','PUT','PATCH','DELETE'];method=st.selectbox('Method',methods,index=methods.index(value.get('method','GET')))
    path=st.text_input('URL path',value.get('path','/records'))
    query=editor('Query parameters',value.get('query',{}),'query');params=editor('Path parameters',value.get('path_params',{}),'params')
    custom=editor('Headers (no credentials)',value.get('headers',{}),'headers');body=editor('Request body',value.get('body'),'body')
    pagination=editor('Pagination',value.get('pagination',{'strategy':'none'}),'pagination')
    advanced=editor('Advanced options',{k:value[k] for k in ('retry','timeout','output_path','capture_raw','content_type','response_format') if k in value},'advanced')
    test=st.button('Save and test request');save=st.button('Save blueprint')
    if save or test:
        payload={**value,**parse(advanced),'name':name,'method':method,'path':path,'connection_id':connection['id'],'query':parse(query),'path_params':parse(params),'headers':parse(custom),'body':parse(body),'pagination':parse(pagination)}
        saved=api('POST','/endpoints',json=payload);st.json(saved)
        if test: show_job(api('POST',f"/endpoints/{saved['id']}/test",json={}))
elif page=='HTTP Request Tester':
    endpoint=choose('endpoints');st.json(endpoint)
    if st.button('Test request'): show_job(api('POST',f"/endpoints/{endpoint['id']}/test",json={}))
    id=st.text_input('Execution ID',st.session_state.get('job_id',''))
    if id and st.button('Refresh response'):
        st.json(api('GET','/jobs/'+id))
        result=httpx.get(base+'/results/'+id,headers=headers)
        if result.is_success: st.json(result.json())
elif page in ('JSON Response Explorer','JSON Transformation Builder'):
    text=editor('JSON input',[], 'json_input');data=parse(text)
    if page=='JSON Response Explorer':
        st.json(data,expanded=False);search=st.text_input('Search keys and values')
        paths=api('POST','/json/inspect',json={'data':data,'search':search});st.dataframe(paths)
        path=st.selectbox('Select JSON path',[p['path'] for p in paths] or ['@']);st.code(path,language=None)
        st.caption('Use the copy button above to copy the path.')
        if st.button('Preview extracted records'): st.json(api('POST','/json/transform',json={'data':data,'config':{'operation':'extract','expression':path}}))
    else:
        op=st.selectbox('Operation',['filter','map','extract','select','rename','flatten','sort','deduplicate','group','aggregate','merge','join','validate','batch'])
        config=editor('Transform configuration',{'operation':op,'expression':'@'},'transform')
        if st.button('Apply transformation'): st.json(api('POST','/json/transform',json={'data':data,'config':parse(config)}))
elif page=='Workflow Builder':
    rows=api('GET','/workflows');selected=st.selectbox('Edit workflow',['New']+[r['id'] for r in rows])
    current=next((r for r in rows if r['id']==selected),{'name':'New workflow','nodes':[{'id':'start','type':'manual','dependencies':[],'config':{}}]})
    text=editor('Workflow node list',current,'workflow');value=parse(text)
    nodes=value.get('nodes',[])
    st.graphviz_chart('digraph workflow {'+';'.join('"'+n['id']+'"' for n in nodes)+';'+';'.join('"'+d+'" -> "'+n['id']+'"' for n in nodes for d in n.get('dependencies',[]))+'}')
    st.caption('Add a node below, or edit the JSON list. References use {{ nodes.fetch.output.data }}; fan-out uses foreach and {{ item.id }}.')
    nid=st.text_input('Node ID');kind=st.selectbox('Node type',['manual','webhook','http','python','filter','transform','merge','conditional','batch','save_json','export_csv','export_parquet'])
    deps=st.multiselect('Dependencies',[n['id'] for n in nodes])
    if kind=='python':
        from components.python_editor import python_config
        node_config=python_config('workflow_python')
    else: node_config=parse(editor('Node configuration',{},'nodeconfig'))
    if st.button('Add node'):
        value['nodes'].append({'id':nid,'type':kind,'dependencies':deps,'config':node_config});st.json(api('POST','/workflows',json=value))
    if st.button('Save workflow'): st.json(api('POST','/workflows',json=value))
elif page=='Workflow Execution':
    w=choose('workflows');input=editor('Trigger input',None,'trigger')
    if st.button('Run workflow'): show_job(api('POST',f"/workflows/{w['id']}/execute",json={'input':parse(input)}))
elif page=='Execution History':
    rows=api('GET','/jobs');st.dataframe(rows)
    if rows:
        job=st.selectbox('Execution',rows,format_func=lambda j:j['id']+' '+j['status']);st.json(job)
        if st.button('Cancel execution'): st.json(api('POST',f"/jobs/{job['id']}/cancel"))
        if job['status']=='completed': st.json(api('GET',f"/results/{job['id']}"))
elif page=='Stored Results':
    rows=api('GET','/results');st.dataframe(rows)
    if rows:
        id=st.selectbox('Result ID',[r['id'] for r in rows]);st.json(api('GET','/results/'+id))
        r=httpx.get(base+f'/results/{id}/download',headers=headers);st.download_button('Download JSONL',r.content,id+'.jsonl')
else:
    st.json(api('GET','/settings'))
    st.subheader('Workflow schedules')
    st.dataframe(api('GET','/schedules'))
    workflow=choose('workflows')
    interval=st.number_input('Interval in seconds',min_value=10,value=3600)
    if st.button('Create schedule'): st.json(api('POST','/schedules',json={'workflow_id':workflow['id'],'interval_seconds':interval}))
    schedule_id=st.text_input('Schedule ID to delete')
    if schedule_id and st.button('Delete schedule'): api('DELETE','/schedules/'+schedule_id);st.rerun()

