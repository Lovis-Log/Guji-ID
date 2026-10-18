"""Explicit one-shot presentation review via the user's existing Claude bridge."""
import json
import os
import subprocess
from pathlib import Path
from app.db import PROJECT_ROOT
from app.collector import now,digest
from app.verification import json_write,redact


def main():
    out=PROJECT_ROOT/'outputs/readable-ui/claude-review';out.mkdir(parents=True,exist_ok=True)
    if (out/'receipt.json').exists():print('Existing review retained; no repeated call.');return
    context=('本任务仅展示层：FastAPI/Jinja2/SQLite，原人物/称呼/来源/证据及逐字段审计均保留。'
      '目前人物来源details直接print record_json/locator_json，证据把original_text中的CBDB JSON放pre。'
      '计划纯presentation模块将CBDB BIOG_MAIN、ALTNAME_DATA、BIOG_ADDR_DATA不同payload形状转为中文facts/定位/书目，未知代码不猜、null不显示。'
      '三类证据：古籍摘录；结构化数据库；其他辅助资料。特别注意C级作者页虽然历史evidence_kind=structured_record，也不是CBDB或古籍原文。'
      '默认摘要；学术details中展示可读来源与版本/页码；独立dialog按需fetch /records/{field|evidence|source}/{id} 完整原始JSON并复制，普通HTML不塞JSON。'
      '技术API必须复用现有公开规则，D/冲突字段与隐藏证据404，不能按名字选择人物。新/sources/{id}只列公开证据及字段。'
      '模板自动escape，dialog用textContent；保留无JS原始JSON链接、Escape关闭/焦点回归。原数据哈希及指纹前后对比，测试杜甫712-770子美、两王珪、C作者页和真原文。')
    request={'task_id':'guji-id-readable-ui-20261017','request_id':'presentation-contract-'+digest(context)[:16],
       'reason':'explicit','prompt':'只审查所给展示重构契约，勿核验历史数据。按桥接四字段JSON返回，summary简短；findings最多4条，每条issue/evidence/risk各最多40字；alternatives=[]；verification最多4条。指出会导致史料误标、JSON泄漏或权限绕过的具体风险，不必泛泛而谈。','context':context}
    json_write(out/'request.json',request)
    receipt={'task_id':request['task_id'],'entry':'Invoke-ClaudeAssist.ps1','started_at':now(),'wrapper_invocations':1,'automatic_retries':0,'scope':'presentation-only review'}
    json_write(out/'receipt.json',receipt)
    bridge=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))/'claude-bridge/Invoke-ClaudeAssist.ps1'
    try:
        p=subprocess.run(['pwsh','-NoProfile','-File',str(bridge),'-TimeoutSeconds','600'],input=json.dumps(request,ensure_ascii=False),text=True,encoding='utf-8',capture_output=True,timeout=630,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        raw=redact(p.stdout);(out/'response.json').write_text(raw,encoding='utf-8');r=json.loads(raw)
        receipt.update(process_exit_code=p.returncode,ok=p.returncode==0 and r.get('ok') is True)
        for k in ('code','model','transport','usage','diagnostic','calls_used','max_calls'): 
            if k in r:receipt[k]=r[k]
    except (OSError,ValueError,subprocess.TimeoutExpired) as e:receipt.update(ok=False,error_type=type(e).__name__)
    receipt['finished_at']=now();json_write(out/'receipt.json',receipt);print(json.dumps(receipt,ensure_ascii=False))


if __name__=='__main__':main()
