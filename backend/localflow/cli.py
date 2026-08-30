"""LocalFlow CLI — 命令行入口"""

from __future__ import annotations

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(prog="localflow", description="LocalFlow AI CLI")
    sub = parser.add_subparsers(dest="command")

    # serve
    p_serve = sub.add_parser("serve", help="启动 API 服务")
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=8765)

    # wizard
    sub.add_parser("wizard", help="运行部署引导（检测硬件+推荐模型）")

    args = parser.parse_args()

    if args.command == "serve":
        import uvicorn
        uvicorn.run(
            "localflow.main:app",
            host=args.host,
            port=args.port,
            reload=False,
        )
    elif args.command == "wizard":
        import asyncio
        from .config import load_config
        from .core.app import LocalFlowApp

        async def run():
            app = LocalFlowApp(config=load_config())
            await app.startup()
            result = await app.wizard.analyze()
            print("\n=== LocalFlow AI 部署向导 ===")
            print(f"平台: {result.hardware.platform}")
            print(f"CPU 核心: {result.hardware.cpu_cores}")
            print(f"内存: {result.hardware.mem_total_mb/1024:.1f} GB")
            if result.hardware.gpus:
                for g in result.hardware.gpus:
                    print(f"GPU {g.index}: {g.name} ({g.vram_total_mb/1024:.1f} GB 显存)")
            else:
                print("GPU: 未检测到独立 GPU")
            print()
            print("--- 推荐模型 ---")
            for r in result.recommendations:
                mark = "★ 推荐" if r.recommended else "   "
                status = "✓" if not r.reason.startswith("显存") else "✗"
                print(f"{mark} {status} {r.name} ({r.quant}) ~{r.size_gb:.1f}GB")
                if r.reason:
                    print(f"    {r.reason}")
            print()
            if not result.can_run_local:
                print(f"⚠ {result.fallback_suggestion}")
            await app.shutdown()

        asyncio.run(run())
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()