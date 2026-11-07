import re, jmespath
PATTERN=re.compile(r'{{\s*(.*?)\s*}}')
def resolve(value,context):
    if isinstance(value,dict): return {k:resolve(v,context) for k,v in value.items()}
    if isinstance(value,list): return [resolve(v,context) for v in value]
    if not isinstance(value,str): return value
    match=PATTERN.fullmatch(value)
    if match: return jmespath.search(match[1],context)
    return PATTERN.sub(lambda m:str(jmespath.search(m[1],context)),value)
