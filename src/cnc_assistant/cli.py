"""Entry point for the `cnc-assistant` command installed by pip."""

import argparse

from . import create_app


def main():
    parser = argparse.ArgumentParser(description="Run the CNC Assistant web app.")
    parser.add_argument("--host", help="Bind address (default: CNC_HOST or 127.0.0.1)")
    parser.add_argument("--port", type=int, help="Port (default: CNC_PORT or 5000)")
    parser.add_argument("--debug", action="store_true", help="Enable auto-reload and the debugger")
    args = parser.parse_args()

    app = create_app()
    app.run(
        host=args.host or app.config["HOST"],
        port=args.port or app.config["PORT"],
        debug=args.debug,
    )


if __name__ == "__main__":
    main()
