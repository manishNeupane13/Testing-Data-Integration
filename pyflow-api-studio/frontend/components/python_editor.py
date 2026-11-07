import json
import streamlit as st

EXAMPLE='''# Input from the previous node is available as data.
# Assign your JSON-compatible output to result.
result = []
for record in data:
    cleaned = dict(record)
    cleaned["name"] = str(record.get("name", "")).strip().title()
    cleaned["valid"] = bool(cleaned["name"])
    result.append(cleaned)
print(f"Checked {len(result)} records")
'''

def python_config(key):
    st.caption('Use data for the incoming JSON. Assign output to result. The default runtime includes pandas, NumPy, openpyxl and the Python standard library.')
    code=st.text_area('Python code',EXAMPLE,height=320,key=key+'_code')
    with st.expander('Syntax preview'): st.code(code,language='python')
    timeout=st.number_input('Time limit (seconds)',min_value=1,max_value=30,value=5,key=key+'_timeout')
    memory=st.selectbox('Memory limit (MiB)',[64,128,256,512],index=1,key=key+'_memory')
    return {'code':code,'timeout':timeout,'memory_mb':memory}

def render_python(api):
    st.subheader('Custom Python')
    status=api('GET','/python/status')
    if status['ready']: st.success('Isolated Python runtime ready')
    else:
        st.warning('Start Docker Desktop and download the Python image before running code.')
        st.code(status['setup'])
    st.caption('Runs in a disposable container: no network, no project files or API credentials, read-only filesystem. Input data is accessible to your code.')
    config=python_config('custom_python')
    source=st.radio('Test input',['Sample JSON','Workbench records'])
    if source=='Workbench records':
        data=st.session_state.get('wb_rows',[]);st.json(data[:5]);st.caption(f'{len(data)} input records')
    else:
        raw=st.text_area('Input JSON','[{"name":" ada "},{"name":""}]',height=120)
        try: data=json.loads(raw)
        except ValueError: st.error('Enter valid JSON input.');data=None
    if st.button('Run Test',type='primary',disabled=not status['ready']):
        # Test API returns categorized failures without stopping the editor.
        output=api('POST','/python/test',json={'config':config,'input':data})
        st.session_state['python_test_result']=output
    if 'python_test_result' in st.session_state:
        output=st.session_state['python_test_result']
        if output['success']:
            st.subheader('Output');st.json(output['output']['data'])
            if output['output'].get('stdout'): st.code(output['output']['stdout'],language=None)
        else:
            st.error(output['error']['category']);st.code(output['error']['message'],language=None)
        st.caption('This preview reflects the last test run.')
    name=st.text_input('Workflow name','Custom Python cleaning')
    if st.button('Save as reusable workflow'):
        workflow=api('POST','/workflows',json={'name':name,'nodes':[
            {'id':'input','type':'manual','config':{}},
            {'id':'custom_python','type':'python','dependencies':['input'],'config':config},
            {'id':'save','type':'save_json','dependencies':['custom_python'],'config':{}}]})
        st.success('Saved workflow: '+workflow['name'])
        st.caption('Run from Workflow Execution with your input JSON, or replace the manual trigger with an HTTP node in Workflow Builder.')
