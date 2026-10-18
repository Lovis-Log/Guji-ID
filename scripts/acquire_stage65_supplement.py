"""Cache a fixed author-page revision; supplementary metadata is never an ancient quote."""
import json
import re
from urllib.request import Request,urlopen
from html.parser import HTMLParser
from app.collector import digest,now
from app.db import PROJECT_ROOT
from app.verification import json_write


class Text(HTMLParser):
    def __init__(self):super().__init__();self.parts=[]
    def handle_data(self,data):self.parts.append(data)


def main():
    root=PROJECT_ROOT/'data/external/stage65-supplement';root.mkdir(parents=True,exist_ok=True)
    url='https://zh.wikisource.org/w/index.php?oldid=2307897'
    rawfile=root/'author-2307897.html'
    if not rawfile.exists():
        with urlopen(Request(url,headers={'User-Agent':'GujiIDCourse/0.65 (non-commercial educational research)'}),timeout=45) as r:raw=r.read()
        rawfile.write_bytes(raw)
    raw=rawfile.read_bytes();parser=Text();parser.feed(raw.decode('utf-8'))
    text=''.join(parser.parts)
    start=text.find('名潛，或名淵明。');end=text.find('以清新自然的詩文著稱於世。',start)
    if start<0 or end<start:raise ValueError('fixed revision text failed explicit marker check')
    paragraph=text[start:end+len('以清新自然的詩文著稱於世。')]
    assert '陶淵明' in text and '365' in text and '427' in text and '潯陽柴桑' in paragraph
    record={'source_id':'S-WS65-2307897','url':url,'version':'Wikisource oldid=2307897','raw_path':str(rawfile.relative_to(PROJECT_ROOT)),
      'sha256':digest(raw),'retrieved_at':now(),'grade':'C','license':'CC-BY-SA-4.0','source_title':'维基文库作者资料页：陶淵明',
      'canonical_source_name':'陶潛','alias':'陶淵明','birth_year':365,'death_year':427,'identity_description':paragraph,'locator':{'text_start':start,'text_end':end,'revision_id':2307897}}
    json_write(root/'record.json',record);print(json.dumps(record,ensure_ascii=False))


if __name__=='__main__':main()
