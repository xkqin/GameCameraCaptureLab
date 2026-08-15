import argparse

from .app import run_app


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Unified Game Camera Capture Studio / 统一游戏相机采集器"
    )
    parser.add_argument(
        "--trajectory-file",
        help="Trajectory JSON/CSV to select and load when the UI starts",
    )
    args = parser.parse_args()
    run_app(trajectory_file=args.trajectory_file)


if __name__ == "__main__":
    main()
