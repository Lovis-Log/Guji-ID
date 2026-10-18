"""One optional, read-only source interpretation through the existing bridge."""
import json
import os
import subprocess
from pathlib import Path

from app.collector import digest, now
from app.db import PROJECT_ROOT
from app.verification import json_write, redact


def main():
    output = PROJECT_ROOT / 'outputs/classical-source/claude-review'
    output.mkdir(parents=True, exist_ok=True)
    if (output / 'receipt.json').exists():
        print('Existing call receipt retained; no repeated model call')
        return
    manifest = json.loads((PROJECT_ROOT / 'data/external/wikisource-jts190b/validated_quotes.json').read_text(encoding='utf-8'))
    context = json.dumps({'book': manifest['book_title'], 'volume': manifest['volume'],
        'fixed_revision': manifest['revision_id'], 'source_url': manifest['revision_url'],
        'verified_cases': [{k: case[k] for k in ('person_id','source_person_id','name','courtesy','quote','start','end','section_anchor')}
                           for case in manifest['cases']]}, ensure_ascii=False)
    request = {'task_id': 'guji-id-original-source-20261018', 'request_id': 'jts190b-pair-'+digest(context)[:16],
        'reason': 'explicit', 'context': context,
        'prompt': '只审查给定两条《舊唐書》原文能否直接支持人物的“字”关系及边界。不能据此自动改籍贯、年份、号或推测作者亲自自称。不要补造原文、书影或链接。必须按桥接四字段JSON返回；summary不超过80字；findings恰好两条，每条issue为person_id，evidence为本输入的直接依据，risk为此句不能证明的内容；alternatives=[]；verification列至多两条程序可检查项。只给建议，Codex仍用原始页面和数据库程序核对。'}
    json_write(output / 'request.json', request)
    receipt = {'task_id': request['task_id'], 'request_id': request['request_id'],
               'entry': 'Invoke-ClaudeAssist.ps1', 'started_at': now(), 'records': 2,
               'wrapper_invocations': 1, 'automatic_retries': 0, 'status': 'started'}
    json_write(output / 'receipt.json', receipt)
    bridge = Path(os.environ.get('CODEX_HOME', str(Path.home()/'.codex'))) / 'claude-bridge/Invoke-ClaudeAssist.ps1'
    try:
        process = subprocess.run(['pwsh','-NoProfile','-File',str(bridge),'-TimeoutSeconds','600'],
            input=json.dumps(request,ensure_ascii=False),text=True,encoding='utf-8',capture_output=True,timeout=630,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        raw = redact(process.stdout)
        (output / 'response.json').write_text(raw,encoding='utf-8')
        result = json.loads(raw)
        receipt.update(process_exit_code=process.returncode,stderr_sha256=digest(process.stderr))
        for field in ('ok','code','model','transport','usage','diagnostic','calls_used','max_calls'):
            if field in result:receipt[field]=result[field]
        findings=result.get('analysis',{}).get('findings',[])
        valid = process.returncode==0 and result.get('ok') is True and len(findings)==2 and {f.get('issue') for f in findings}=={c['person_id'] for c in manifest['cases']}
        receipt.update(status='completed' if valid else 'failed_or_invalid_output',validated_records=2 if valid else 0)
        if valid:
            json_write(output / 'validated.json', {'findings': findings, 'advisory_only': True,
               'program_source_manifest': str((PROJECT_ROOT/'data/external/wikisource-jts190b/validated_quotes.json').resolve())})
    except (OSError,ValueError,subprocess.TimeoutExpired) as exc:
        receipt.update(status='failed',error_type=type(exc).__name__,validated_records=0)
    receipt['finished_at']=now()
    json_write(output/'receipt.json',receipt)
    print(json.dumps(receipt,ensure_ascii=False))


if __name__=='__main__':main()
