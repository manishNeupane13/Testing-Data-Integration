import sqlite3, json, threading
from pathlib import Path
from datetime import datetime, timezone
class Repository:
    def __init__(self, directory):
        self.directory=Path(directory); self.directory.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.directory/'studio.db',check_same_thread=False)
        self.lock=threading.RLock()
        self.db.execute('CREATE TABLE IF NOT EXISTS objects (kind TEXT,id TEXT,value TEXT,PRIMARY KEY(kind,id))')
        self.db.execute('CREATE TABLE IF NOT EXISTS revisions (kind TEXT,id TEXT,version INTEGER,value TEXT,PRIMARY KEY(kind,id,version))')
        self.db.commit()
    def put(self,kind,value):
        with self.lock:
            old=self.get(kind,value['id'])
            if kind in ('connections','endpoints','workflows','recipes'):
                value={**value,'version':(old or {}).get('version',0)+1}
                self.db.execute('INSERT INTO revisions VALUES (?,?,?,?)',(kind,value['id'],value['version'],json.dumps(value)))
            self.db.execute('INSERT OR REPLACE INTO objects VALUES (?,?,?)',(kind,value['id'],json.dumps(value)))
            self.db.commit()
        return value
    def get(self,kind,id):
        with self.lock:
            row=self.db.execute('SELECT value FROM objects WHERE kind=? AND id=?',(kind,id)).fetchone()
        return json.loads(row[0]) if row else None
    def list(self,kind):
        with self.lock: rows=self.db.execute('SELECT value FROM objects WHERE kind=?',(kind,)).fetchall()
        return [json.loads(r[0]) for r in rows]
    def delete(self,kind,id):
        with self.lock:
            self.db.execute('DELETE FROM objects WHERE kind=? AND id=?',(kind,id));self.db.commit()
    def recover(self):
        for job in self.list('jobs'):
            if job['status'] in ('queued','running'):
                self.put('jobs',{**job,'status':'interrupted','error':'Server restarted; submit a new run'})
    def write_result(self,id,data):
        path=self.directory/(id+'.jsonl')
        with path.open('w') as f:
            for row in data if isinstance(data,list) else [data]: f.write(json.dumps(row,default=str)+'\n')
        return str(path)
