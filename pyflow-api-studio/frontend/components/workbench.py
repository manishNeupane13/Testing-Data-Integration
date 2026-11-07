import json
from uuid import uuid4
import streamlit as st

DEMO=[{'driver_id':' D001 ','name':' Ada ','active':'yes','distance':'120'},
      {'driver_id':'D002','name':'','active':'yes','distance':'-5'},
      {'driver_id':'D002','name':' Lin ','active':'no','distance':'bad'}]

def render_workbench(api):
    st.subheader('Data Workbench')
    st.caption('Import or fetch → clean → validate every row → search, sort and download a report.')
    ss=st.session_state
    ss.setdefault('wb_rows',DEMO)
    ss.setdefault('wb_recipe',{'id':str(uuid4()),'name':'Driver checks','steps':[], 'rules':[]})
    source=st.radio('Data source',['Import file','Saved endpoint','Stored result','Demo records'],horizontal=True)
    def load(rows):
        if not isinstance(rows,list) or any(not isinstance(r,dict) for r in rows):
            st.error('Select an array of records first.');return
        ss['wb_rows']=rows;ss.pop('wb_report',None);st.success(f'Loaded {len(rows)} records')
    if source=='Import file':
        upload=st.file_uploader('Import CSV, JSON or JSONL',type=['csv','json','jsonl'])
        if upload and st.button('Load file'):
            try: text=upload.getvalue().decode('utf-8-sig')
            except UnicodeDecodeError: st.error('Use a UTF-8 encoded file.');st.stop()
            load(api('POST','/data/import',json={'text':text,'format':upload.name.rsplit('.',1)[-1].lower()})['data'])
    elif source=='Saved endpoint':
        endpoints=api('GET','/endpoints')
        if endpoints:
            endpoint=st.selectbox('Fetch from endpoint',endpoints,format_func=lambda e:e['name'])
            params=st.text_area('Dynamic input values (JSON)', '{}',help='Use {{ input.driver_id }} or other input references in your endpoint blueprint.')
            if st.button('Fetch latest data'):
                try: values=json.loads(params)
                except ValueError: st.error('Enter valid JSON input.');st.stop()
                job=api('POST',f"/endpoints/{endpoint['id']}/execute",json={'input':values})
                ss['wb_fetch_job']=job['id']
            if ss.get('wb_fetch_job'):
                job=api('GET','/jobs/'+ss['wb_fetch_job']);st.write('Fetch status:',job['status'])
                if st.button('Refresh fetch status'): st.rerun()
                if job['status']=='completed':
                    result=api('GET','/results/'+job['id'])['result']
                    path=st.text_input('Records expression','data',help='For a Sheets response this might be data.values; tabular rows must be converted to objects before processing.')
                    if st.button('Load fetched records'):
                        load(api('POST','/json/transform',json={'data':result,'config':{'operation':'extract','expression':path}})['data'])
                elif job['status']=='failed': st.json(job.get('error'))
        else: st.info('Create a connection and endpoint blueprint, then fetch its data here.')
    elif source=='Stored result':
        results=api('GET','/results')
        if results:
            id=st.selectbox('Stored execution',[r['id'] for r in results]);path=st.text_input('Records expression','data')
            if st.button('Load stored records'):
                result=api('GET','/results/'+id)['result']
                load(api('POST','/json/transform',json={'data':result,'config':{'operation':'extract','expression':path}})['data'])
        else: st.info('Run an endpoint or save a report first.')
    elif st.button('Load demo records'): load(DEMO)

    st.write(f"{len(ss['wb_rows'])} source records")
    st.dataframe(ss['wb_rows'][:200],width='stretch')
    st.caption('Preview shows the first 200 records. Processing checks all loaded records, up to 100,000.')
    if st.button('Flatten nested fields'):
        load(api('POST','/json/transform',json={'data':ss['wb_rows'],'config':{'operation':'flatten'}})['data'])
        st.rerun()
    fields=list(dict.fromkeys(k for row in ss['wb_rows'] for k in row))
    if not fields: st.info('Load records to choose fields.');return

    with st.expander('Load a reusable recipe'):
        recipes=api('GET','/recipes')
        if recipes:
            selected=st.selectbox('Recipe',recipes,format_func=lambda r:r['name'])
            if st.button('Load recipe'): ss['wb_recipe']=selected;ss.pop('wb_report',None);st.rerun()
    recipe=ss['wb_recipe']
    recipe['name']=st.text_input('Recipe name',recipe['name'],key='wb_recipe_name_'+recipe['id'])
    cleaning,validation=st.tabs(['Cleaning steps','Validation checks'])
    with cleaning:
        field=st.selectbox('Field to clean',fields)
        op=st.selectbox('Cleaning action',['trim','lower','upper','fill','cast','replace'])
        step={'field':field,'operation':op}
        if op=='cast': step['type']=st.selectbox('Convert to',['string','number','integer','boolean'])
        if op in ('fill','replace'): step['value']=st.text_input('Replacement value')
        if op=='replace': step['find']=st.text_input('Text to replace')
        if st.button('Add cleaning step'): recipe['steps'].append(step);ss.pop('wb_report',None)
        st.dataframe(recipe['steps'],width='stretch')
        if st.button('Clear cleaning steps'): recipe['steps']=[];ss.pop('wb_report',None);st.rerun()
    with validation:
        field=st.selectbox('Field to validate',fields)
        check=st.selectbox('Check',['required','type','unique','min','max','allowed','condition'])
        rule={'field':field,'check':check}
        if check=='type': rule['type']=st.selectbox('Expected type',['string','number','integer','boolean'])
        if check in ('min','max'): rule['value']=st.number_input('Threshold',value=0.0)
        if check=='allowed': rule['values']=[v.strip() for v in st.text_input('Allowed values (comma separated)').split(',')]
        if check=='condition':
            rule['when']=st.text_input('IF expression (JMESPath)','active == `true`')
            rule['expression']=st.text_input('THEN must be true','distance >= `0`')
            st.caption('The check applies only to rows matching IF. Expressions cannot execute Python.')
        rule['message']=st.text_input('Failure reason',f'{field}: {check} check failed',key=f'wb_reason_{field}_{check}')
        if st.button('Add validation check'): recipe['rules'].append(rule);ss.pop('wb_report',None)
        st.dataframe(recipe['rules'],width='stretch')
        if st.button('Clear validation checks'): recipe['rules']=[];ss.pop('wb_report',None);st.rerun()
    if st.button('Save reusable recipe'): ss['wb_recipe']=api('POST','/recipes',json=recipe);st.success('Recipe saved')
    st.download_button('Export recipe',json.dumps(recipe,indent=2),'recipe.json')
    imported=st.file_uploader('Import recipe JSON',type=['json'],key='wb_recipe_upload')
    if imported and st.button('Load imported recipe'):
        try: value=json.load(imported)
        except ValueError: st.error('Invalid JSON recipe.');st.stop()
        ss['wb_recipe']=api('POST','/recipes',json=value);ss.pop('wb_report',None);st.rerun()

    if st.button('Create reusable workflow from recipe'):
        start={'id':'source','type':'manual','dependencies':[],'config':{}}
        if source=='Saved endpoint' and endpoints:
            start={'id':'source','type':'http','dependencies':[],'config':{'endpoint_id':endpoint['id']}}
        workflow={'name':recipe['name']+' workflow','nodes':[start,
            {'id':'check','type':'transform','dependencies':['source'],'config':{'operation':'validate_records','recipe':recipe}},
            {'id':'report','type':'save_json','dependencies':['check'],'config':{}}]}
        saved=api('POST','/workflows',json=workflow)
        st.success('Workflow saved: '+saved['name'])
        st.caption('Run it from Workflow Execution. For an import workflow, supply the records as trigger input. The workflow keeps a snapshot of this recipe.')

    st.subheader('Inspect and report')
    search=st.text_input('Search records (any field)')
    sort=st.selectbox('Sort by',['Original order']+fields)
    descending=st.checkbox('Descending sort')
    save=st.checkbox('Store report in execution results',value=True)
    st.caption('Search and sort change the displayed records. Validation totals always cover the full dataset.')
    if st.button('Run cleaning and validation',type='primary'):
        ss['wb_report']=api('POST','/data/process',json={'data':ss['wb_rows'],'recipe':ss['wb_recipe'],'search':search,'sort_field':None if sort=='Original order' else sort,'descending':descending,'save':save})
    if 'wb_report' in ss:
        report=ss['wb_report'];summary=report['summary']
        for col,key in zip(st.columns(4),['total','passed','failed','cleaned_values']): col.metric(key.replace('_',' ').title(),summary[key])
        views,issues,passed,failed=st.tabs(['Search / sorted view','Issues','Passed records','Failed records'])
        with views: st.dataframe(report['view'],width='stretch')
        with issues: st.dataframe(report['issues'],width='stretch')
        with passed: st.dataframe(report['passed'],width='stretch')
        with failed: st.json(report['failed'],expanded=False)
        st.caption('Report reflects the last run. Run again after changing controls.')
        st.download_button('Download full report',json.dumps(report,indent=2),'validation-report.json')
        st.download_button('Download passed records (JSONL)','\n'.join(json.dumps(row) for row in report['passed']),'passed.jsonl')
        st.download_button('Download failed records (JSONL)','\n'.join(json.dumps(row) for row in report['failed']),'failed.jsonl')
        if report.get('result_id'): st.caption('Stored result ID: '+report['result_id'])
