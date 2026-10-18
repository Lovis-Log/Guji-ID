"""One state maintenance operation explicitly authorized by the user on 2026-10-17.

Preserves the failed attempt, total calls, budget, fingerprints and request history.
This script does not invoke a model or modify provider/authentication settings.
"""
import hashlib
import json
import os
from datetime import datetime,timezone
from pathlib import Path
from app.db import PROJECT_ROOT


def main():
    task='guji-id-stage65-20261017'
    root=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))
    path=root/'claude-bridge/state'/(hashlib.sha256(task.encode()).hexdigest()+'.json')
    lock=path.with_suffix('.json.lock')
    # No concurrent call is running. Fail if the exact audited state changed.
    state=json.loads(path.read_text(encoding='utf-8-sig'))
    if state.get('calls')!=1 or state.get('stopped') is not True or state.get('authorization_base_limit')!=5:
        raise ValueError('state differs from explicitly authorized 1/5 stopped state')
    if any(e.get('code')=='USER_AUTHORIZED_RESUME_STAGE65' for e in state['events']):raise ValueError('resume already used')
    audit=PROJECT_ROOT/'outputs/stage65-release/deepseek_resume_authorization.json'
    if audit.exists():raise ValueError('authorization already recorded')
    before=path.read_bytes()
    backup=PROJECT_ROOT/'outputs/stage65-release/deepseek_state_before_authorized_resume.json'
    backup.write_bytes(before)
    event={'code':'USER_AUTHORIZED_RESUME_STAGE65','utc':datetime.now(timezone.utc).isoformat(),
      'calls':state['calls'],'authorization':'用户明确选择：授权解除停止并再调用一次','budget_unchanged':5}
    state['events'].append(event);state['stopped']=False
    path.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
    audit.write_text(json.dumps({'task_id':task,'event':event,'backup':str(backup),'before_sha256':hashlib.sha256(before).hexdigest()},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'task_id':task,'calls':state['calls'],'stopped':state['stopped'],'max_calls':5},ensure_ascii=False))


if __name__=='__main__':main()
