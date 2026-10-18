import argparse
import json
from app.source_acquisition import acquire_cbdb

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="从官方发布索引下载许可范围内CBDB历史SQLite，验证SHA-256并保留许可快照")
    parser.add_argument("--output", default="data/external/cbdb-stage6")
    parser.add_argument("--version", help="可选官方独立历史版日期；仍须检查日期和实际人物量边界")
    args = parser.parse_args()
    print(json.dumps(acquire_cbdb(args.output, version=args.version), ensure_ascii=False, indent=2))
