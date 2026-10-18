"""Record the user's explicit grant of five further calls, retaining all history."""
import hashlib
import json
import os
from pathlib import Path
from app.db import PROJECT_ROOT
from app.collector import now


def main():
    root=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))
    path=root/'claude-bridge/state'/(hashlib.sha256(b'guji-id-stage65-20261017').hexdigest()+'.json')
    state=json.loads(path.read_text(encoding='utf-8-sig'))
    assert state['calls']==5 and state['stopped'] is True and state.get('authorized_extra_calls',0)==0
    out=PROJECT_ROOT/'outputs/stage65-release';backup=out/'deepseek_state_before_extra_authorization.json'
    assert not backup.exists();backup.write_bytes(path.read_bytes())
    state['authorization_base_limit']=5;state['authorized_extra_calls']=5;state['stopped']=False
    event={'code':'USER_AUTHORIZED_FIVE_EXTRA_CALLS','utc':now(),'calls':5,'base_limit':5,'authorized_extra_calls':5,
      'authorization':'用户明确表示：我授权你接下来还可以调用5次。','previous_history_retained':True}
    state['events'].append(event)
    path.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'deepseek_extra_authorization.json').write_text(json.dumps({'event':event,'backup':str(backup),'new_total_ceiling':10},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'calls_used':5,'total_ceiling':10,'remaining':5,'stopped':False}))


if __name__=='__main__':main()
