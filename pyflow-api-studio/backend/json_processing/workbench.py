"""Reusable import, cleaning and row-level validation pipeline."""
import copy
import csv
import io
import json
import math
from collections import Counter, defaultdict
import jmespath
from pydantic import BaseModel, Field

class Recipe(BaseModel):
    id: str = Field(pattern=r'^[A-Za-z0-9_-]+$')
    name: str
    steps: list[dict] = Field(default_factory=list)
    rules: list[dict] = Field(default_factory=list)
    version: int = 1

class DataWorkbench:
    LIMIT = 100000
    def import_text(self, text, format):
        if len(text.encode()) > 10*1024*1024:
            raise ValueError('Import exceeds 10 MiB')
        if format == 'csv': rows = list(csv.DictReader(io.StringIO(text)))
        elif format == 'jsonl': rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        elif format == 'json': rows = json.loads(text)
        else: raise ValueError('Unsupported format')
        self.validate_rows(rows)
        return rows

    def validate_rows(self, rows):
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError('Select an array of JSON objects')
        if len(rows) > self.LIMIT: raise ValueError('Too many records; maximum 100000')

    def cast(self, value, kind):
        if kind == 'string': return str(value)
        if kind == 'number':
            number = float(value)
            if not math.isfinite(number): raise ValueError('Number must be finite')
            return number
        if kind == 'integer':
            from decimal import Decimal, InvalidOperation
            try: number=Decimal(str(value))
            except InvalidOperation: raise ValueError('Expected an integer') from None
            if not number.is_finite() or number!=number.to_integral_value(): raise ValueError('Expected an integer')
            return int(number)
        if kind == 'boolean':
            if str(value).lower() in ('true','1','yes'): return True
            if str(value).lower() in ('false','0','no'): return False
            raise ValueError('Expected true/false, yes/no or 1/0')
        raise ValueError('Unknown type')

    def run(self, rows, recipe, search='', sort_field=None, descending=False):
        self.validate_rows(rows)
        data=copy.deepcopy(rows);issues=[];changes=0
        for step in recipe.steps:
            op=step['operation'];field=step.get('field')
            if op not in ('trim','lower','upper','fill','cast','replace'): raise ValueError('Unknown cleaning operation')
            for i,row in enumerate(data):
                old=row.get(field);value=old
                try:
                    if op=='fill' and (old is None or old==''): value=step.get('value')
                    elif old is not None:
                        if op=='trim': value=str(old).strip()
                        elif op=='lower': value=str(old).lower()
                        elif op=='upper': value=str(old).upper()
                        elif op=='replace': value=str(old).replace(str(step.get('find','')),str(step.get('value','')))
                        elif op=='cast': value=self.cast(old,step['type'])
                    if value!=old or type(value)!=type(old): changes+=1;row[field]=value
                except (TypeError,ValueError) as exc:
                    issues.append({'row':i+1,'field':field,'rule':'cleaning:'+op,'reason':str(exc)})
        allowed={'required','type','min','max','allowed','unique','condition'}
        for rule in recipe.rules:
            kind=rule['check'];field=rule.get('field');expression=rule.get('expression','@')
            if kind not in allowed: raise ValueError('Unknown validation check')
            counts=Counter(json.dumps(row.get(field),sort_keys=True) for row in data) if kind=='unique' else {}
            for i,row in enumerate(data):
                value=row.get(field);valid=True
                if kind=='required': valid=value is not None and (not isinstance(value,str) or bool(value.strip()))
                elif kind=='type':
                    types={'string':lambda v:isinstance(v,str),'number':lambda v:isinstance(v,(int,float)) and not isinstance(v,bool),'integer':lambda v:isinstance(v,int) and not isinstance(v,bool),'boolean':lambda v:isinstance(v,bool)}
                    valid=types[rule['type']](value)
                elif kind in ('min','max'):
                    try:
                        numeric=float(value);valid=math.isfinite(numeric) and (numeric>=float(rule['value']) if kind=='min' else numeric<=float(rule['value']))
                    except (TypeError,ValueError): valid=False
                elif kind=='allowed': valid=value in rule['values']
                elif kind=='unique': valid=counts[json.dumps(value,sort_keys=True)]==1
                elif kind=='condition':
                    # A when expression is the IF; expression is the required THEN condition.
                    valid=not jmespath.search(rule.get('when','`true`'),row) or bool(jmespath.search(expression,row))
                if not valid: issues.append({'row':i+1,'field':field or '@','rule':kind,'reason':rule.get('message',f'{field or expression}: {kind} check failed')})
        by_row=defaultdict(list)
        for issue in issues: by_row[issue['row']].append(issue)
        failed_ids=set(by_row)
        passed=[row for i,row in enumerate(data,1) if i not in failed_ids]
        failed=[{'row':i,'record':row,'issues':by_row[i]} for i,row in enumerate(data,1) if i in failed_ids]
        visible=[row for row in data if search.casefold() in json.dumps(row,ensure_ascii=False).casefold()]
        if sort_field:
            def key(row):
                value=row.get(sort_field)
                if value is None: return (2,'')
                if isinstance(value,(int,float)) and not isinstance(value,bool): return (0,value)
                return (1,str(value).casefold())
            visible.sort(key=key,reverse=descending)
        return {'summary':{'total':len(data),'passed':len(passed),'failed':len(failed),'issues':len(issues),'cleaned_values':changes,'visible':len(visible)},'data':data,'view':visible,'passed':passed,'failed':failed,'issues':issues}
