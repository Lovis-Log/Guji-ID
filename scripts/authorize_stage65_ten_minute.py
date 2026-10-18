"""Record the explicit post-timeout authorization of up to five further calls."""
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
    assert state['calls']==6 and state['stopped'] is True
    out=PROJECT_ROOT/'outputs/stage65-release';backup=out/'deepseek_state_before_ten_minute_authorization.json'
    assert not backup.exists();backup.write_bytes(path.read_bytes())
    # Exactly five remaining attempts under this latest grant, including old unused slots.
    ceiling=state['calls']+5
    state['authorization_base_limit']=5;state['authorized_extra_calls']=ceiling-5;state['stopped']=False
    event={'code':'USER_AUTHORIZED_TEN_MINUTE_CONTINUATION','utc':now(),'calls':state['calls'],'new_total_ceiling':ceiling,
      'new_remaining_limit':5,'timeout_seconds':600,'authorization':'用户：超时限制改为10min，现在授权继续调用claude5次。','history_retained':True}
    state['events'].append(event)
    path.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'deepseek_ten_minute_authorization.json').write_text(json.dumps({'event':event,'backup':str(backup)},ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'calls_used':state['calls'],'total_ceiling':ceiling,'remaining':5,'stopped':False}))


if __name__=='__main__':main()
