"""Run a fresh synthetic library; refuse to overlay a previous demo or backups."""
import argparse
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--directory',type=Path,default=ROOT/'.test-data/demo')
    parser.add_argument('--port',type=int,default=8765);args=parser.parse_args()
    target=args.directory.resolve()
    if not target.is_relative_to((ROOT/'.test-data').resolve()) or target.exists():
        raise SystemExit('Choose a NEW directory inside this repository .test-data; nothing overwritten')
    if not 0<=args.port<=65535:raise SystemExit('Invalid port')
    os.environ['XAGON_ALLOW_LIVE_API']='0'
    from xagon.product_store import ProductStore
    from xagon.alpha import create_server
    store=ProductStore(target/'learning.sqlite3')
    store.import_file(ROOT/'examples/first_lesson.md',title='Synthetic demo only')
    server=create_server(port=args.port,store=store)
    print(f'Synthetic demo only: http://127.0.0.1:{server.server_port}/',flush=True)
    try:server.serve_forever(poll_interval=.25)
    except KeyboardInterrupt:pass
    finally:server.server_close()


if __name__=='__main__':main()
