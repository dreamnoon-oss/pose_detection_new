"""Launch the local web UI for the driver-behaviour analysis system.

Usage:
    python run_web.py            # http://127.0.0.1:8000
    python run_web.py --port 8000
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn

from server.main import app


def main():
    parser = argparse.ArgumentParser(description="地铁司机行为分析 Web 服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="开发模式热重载")
    args = parser.parse_args()

    print("=" * 60)
    print("  地铁司机标准化作业行为智能分析系统")
    print(f"  访问: http://{args.host}:{args.port}")
    print("=" * 60)
    uvicorn.run(app, host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
