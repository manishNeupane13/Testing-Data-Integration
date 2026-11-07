import json, jmespath, jsonschema
from collections import defaultdict

def flatten(obj,prefix=''):
    result={}
    for k,v in obj.items():
        key=f'{prefix}.{k}' if prefix else k
        if isinstance(v,dict): result.update(flatten(v,key))
        else: result[key]=v
    return result
class JSONEngine:
    def apply(self,data,config):
        op=config.get('operation','map');expr=config.get('expression','@')
        if op=='validate_records':
            from backend.json_processing.workbench import DataWorkbench, Recipe
            return DataWorkbench().run(data,Recipe.model_validate(config['recipe']))
        if op in ('map','extract','filter'): return jmespath.search(expr,data)
        if op=='validate': jsonschema.validate(data,config['schema']);return data
        if op=='batch':
            size=int(config.get('size',100))
            if size<=0: raise ValueError('Batch size must be positive')
            return [data[i:i+size] for i in range(0,len(data),size)]
        if op=='flatten': return [flatten(row) for row in data]
        if op=='select': return [{k:jmespath.search(v,row) for k,v in config['fields'].items()} for row in data]
        if op=='rename': return [{config['fields'].get(k,k):v for k,v in row.items()} for row in data]
        if op=='sort': return sorted(data,key=lambda row:jmespath.search(expr,row),reverse=config.get('reverse',False))
        if op=='deduplicate':
            seen=set();result=[]
            for row in data:
                key=json.dumps(jmespath.search(expr,row),sort_keys=True)
                if key not in seen: result.append(row);seen.add(key)
            return result
        if op in ('group','aggregate'):
            groups=defaultdict(list)
            for row in data: groups[str(jmespath.search(expr,row))].append(row)
            if op=='group': return dict(groups)
            method=config.get('method','count');field=config.get('field','@')
            def aggregate(rows):
                values=[jmespath.search(field,r) for r in rows]
                return {'count':lambda:len(rows),'sum':lambda:sum(values),'mean':lambda:sum(values)/len(values),'min':lambda:min(values),'max':lambda:max(values)}[method]()
            return {key:aggregate(rows) for key,rows in groups.items()}
        if op=='merge': return [row for dataset in data for row in dataset]
        if op=='join':
            left,right=data;index=defaultdict(list)
            for row in right: index[jmespath.search(config['right_key'],row)].append(row)
            return [{**l,**r} for l in left for r in index[jmespath.search(config['left_key'],l)]]
        raise ValueError('Unknown JSON operation')

def inspect_paths(value,prefix=''):
    yield {'path':prefix or '@','preview':str(value)[:150]}
    if isinstance(value,dict):
        for key,child in value.items():
            path=(prefix+'.' if prefix else '')+json.dumps(key)
            yield from inspect_paths(child,path)
    elif isinstance(value,list):
        for i,child in enumerate(value[:100]): yield from inspect_paths(child,f'{prefix}[{i}]')
