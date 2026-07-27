from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Optional

import typer
from rich.table import Table

from .align_pose import align_pose as align_pose_file
from .config import AppConfig, load_config
from .laion_setup import ensure_laion_repo, verify_laion_repo
from .lua_control import LuaControl, make_session_id
from .lua_patcher import (
    backup_lua as backup_lua_file,
    check_lua as check_lua_file,
    patch_lua_logger as patch_lua_logger_file,
    restore_lua as restore_lua_file,
    verify_lua_patch as verify_lua_patch_file,
)
from .obs_control import OBSController, connect_obs, find_latest_video_file
from .paths import PROJECT_ROOT, ensure_dir, resolve_project_path
from .report import generate_report
from .utils import console, make_unique_dir, setup_logging
from .video_extract import extract_frames as extract_video_frames


app = typer.Typer(help="Manual RE9 FreeCam OBS recorder with LAION aesthetic pose analysis.")


def _config(config: Optional[Path]) -> AppConfig:
    return load_config(config)


def _output_dir(config: AppConfig, stem: str, overwrite: bool = False) -> Path:
    base = config.output_dir
    base.mkdir(parents=True, exist_ok=True)
    expected = ["scores.csv", "scores_with_pose.csv", "score_curve.png", "camera_path.png", "report.html"]
    if overwrite or not any((base / item).exists() for item in expected):
        return base
    return make_unique_dir(base, stem)


def _pose_log_for_session(config: AppConfig, session_id: str) -> Path:
    base = config.pose_log_file
    return base.with_name(f"{base.stem}_{session_id}{base.suffix}")


@app.command("check-lua")
def check_lua(config: Optional[Path] = typer.Option(None, "--config")) -> None:
    cfg = _config(config)
    status = check_lua_file(cfg)
    if status.exists:
        console.print(f"[green]OK[/green] {status.lua_path}")
        console.print(f"Patched: {status.patched}")
    else:
        console.print(f"[red]Missing[/red] {status.message}")
        raise typer.Exit(1)


@app.command("backup-lua")
def backup_lua(config: Optional[Path] = typer.Option(None, "--config")) -> None:
    cfg = _config(config)
    backup = backup_lua_file(cfg)
    console.print(f"[green]Backup created:[/green] {backup}")


@app.command("patch-lua-logger")
def patch_lua_logger(config: Optional[Path] = typer.Option(None, "--config")) -> None:
    cfg = _config(config)
    lua_path, backup = patch_lua_logger_file(cfg)
    console.print(f"[green]Patched Lua logger:[/green] {lua_path}")
    console.print(f"Backup: {backup}")


@app.command("restore-lua")
def restore_lua(backup: Path = typer.Option(..., "--backup"), config: Optional[Path] = typer.Option(None, "--config")) -> None:
    cfg = _config(config)
    restored = restore_lua_file(cfg, backup)
    console.print(f"[green]Restored Lua file:[/green] {restored}")


@app.command("verify-lua-patch")
def verify_lua_patch(config: Optional[Path] = typer.Option(None, "--config")) -> None:
    cfg = _config(config)
    status = verify_lua_patch_file(cfg)
    color = "green" if status.patched else "red"
    console.print(f"[{color}]{status.message}[/{color}]")
    if not status.patched:
        raise typer.Exit(1)


@app.command("setup-laion")
def setup_laion(config: Optional[Path] = typer.Option(None, "--config")) -> None:
    cfg = _config(config)
    repo = ensure_laion_repo(cfg.raw["laion"]["repo_url"], cfg.laion_repo_dir)
    console.print(f"[green]LAION aesthetic-predictor ready:[/green] {repo}")


@app.command("obs-test")
def obs_test(
    obs_password: str = typer.Option("", "--obs-password"),
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    cfg = _config(config)
    obs_cfg = cfg.raw["obs"]
    password = obs_password or obs_cfg.get("password", "")
    client = connect_obs(obs_cfg["host"], int(obs_cfg["port"]), password)
    version = client.get_version()
    status = client.get_record_status()
    console.print("[green]Connected to OBS WebSocket.[/green]")
    console.print(version)
    console.print(status)


@app.command("start-pose-log")
def start_pose_log(config: Optional[Path] = typer.Option(None, "--config")) -> None:
    cfg = _config(config)
    session_id = make_session_id()
    pose_log = _pose_log_for_session(cfg, session_id)
    control = LuaControl(cfg)
    path = control.write_start_control(session_id, pose_log, float(cfg.raw["lua_logger"]["default_interval_sec"]))
    console.print(f"[green]Wrote start control:[/green] {path}")
    console.print(f"Session: {session_id}")
    console.print(f"Pose log: {pose_log}")


@app.command("stop-pose-log")
def stop_pose_log(
    session_id: Optional[str] = typer.Option(None, "--session-id"),
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    cfg = _config(config)
    control = LuaControl(cfg)
    if session_id is None:
        status = control.read_status() or {}
        session_id = str(status.get("session_id") or "")
    if not session_id:
        console.print("[red]No session id was provided and no Lua status session was found.[/red]")
        raise typer.Exit(1)
    path = control.write_stop_control(session_id)
    console.print(f"[green]Wrote stop control:[/green] {path}")


@app.command("physics-probe")
def physics_probe(
    session_id: str = typer.Option("manual", "--session-id"),
    wait_sec: float = typer.Option(1.0, "--wait-sec"),
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    """Ask the Lua patch to run a small via.physics castRay probe at the current FreeCam pose."""
    cfg = _config(config)
    control = LuaControl(cfg)
    path = control.write_physics_probe_control(session_id)
    console.print(f"[green]Wrote physics probe control:[/green] {path}")
    deadline = time.monotonic() + wait_sec
    status = control.read_status() or {}
    while time.monotonic() < deadline:
        current = control.read_status() or {}
        if current.get("physics_probe_status") != status.get("physics_probe_status") or current.get("physics_probe_rays"):
            status = current
            break
        status = current
        time.sleep(0.1)
    table = Table(title="Lua Physics Probe")
    table.add_column("Field")
    table.add_column("Value")
    for key in [
        "physics_probe_status",
        "physics_probe_pose_valid",
        "physics_probe_contacts",
        "physics_probe_rays",
        "physics_probe_details",
        "physics_probe_error",
        "last_error",
    ]:
        table.add_row(key, str(status.get(key, "")))
    console.print(table)


@app.command("interactive-record")
def interactive_record(
    obs_password: str = typer.Option("", "--obs-password"),
    analyze: bool = typer.Option(False, "--analyze"),
    fps: Optional[float] = typer.Option(None, "--fps"),
    device: Optional[str] = typer.Option(None, "--device"),
    top_k: Optional[int] = typer.Option(None, "--top-k"),
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    cfg = _config(config)
    video_path, pose_log, session_id = _record_only(cfg, obs_password)
    console.print(f"[green]Video:[/green] {video_path}")
    console.print(f"[green]Pose log:[/green] {pose_log}")
    if analyze:
        _analyze_video_pipeline(
            cfg,
            video_path=video_path,
            pose_log=pose_log,
            fps=fps or float(cfg.raw["video"]["fps"]),
            device=device or cfg.raw["laion"]["device"],
            top_k=top_k or int(cfg.raw["report"]["top_k"]),
            session_id=session_id,
        )


@app.command("one-click-record")
def one_click_record(
    obs_password: str = typer.Option("", "--obs-password"),
    live_score: bool = typer.Option(True, "--live-score/--no-live-score"),
    live_score_interval: float = typer.Option(2.0, "--live-score-interval"),
    live_summary_window: Optional[float] = typer.Option(None, "--live-summary-window"),
    segment_window: Optional[float] = typer.Option(None, "--segment-window"),
    device: Optional[str] = typer.Option(None, "--device"),
    topmost: bool = typer.Option(True, "--topmost/--no-topmost"),
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    """Open a small Start/Stop button for OBS recording and Lua pose logging."""
    cfg = _config(config)
    from .record_gui import run_one_click_recorder

    run_one_click_recorder(
        cfg,
        obs_password=obs_password,
        live_score=live_score,
        live_score_interval=live_score_interval,
        live_summary_window_sec=live_summary_window or float(cfg.raw.get("live_score", {}).get("summary_window_sec", 10.0)),
        segment_window_sec=segment_window or float(cfg.raw.get("live_score", {}).get("segment_window_sec", 5.0)),
        device=device or cfg.raw["laion"]["device"],
        topmost=topmost,
    )


@app.command("scan-region")
def scan_region(
    x_min: float = typer.Option(..., "--x-min"),
    x_max: float = typer.Option(..., "--x-max"),
    z_min: float = typer.Option(..., "--z-min"),
    z_max: float = typer.Option(..., "--z-max"),
    y: float = typer.Option(..., "--y"),
    obs_password: str = typer.Option("", "--obs-password"),
    points_x: int = typer.Option(5, "--points-x"),
    points_z: int = typer.Option(3, "--points-z"),
    yaw_step: float = typer.Option(30.0, "--yaw-step"),
    segment_seconds: float = typer.Option(7.0, "--segment-seconds"),
    settle_seconds: float = typer.Option(0.0, "--settle-seconds"),
    post_stop_seconds: float = typer.Option(1.5, "--post-stop-seconds"),
    pitch_deg: float = typer.Option(0.0, "--pitch-deg"),
    fov: Optional[float] = typer.Option(None, "--fov"),
    max_segments: Optional[int] = typer.Option(None, "--max-segments"),
    start_segment: int = typer.Option(1, "--start-segment"),
    session_id: Optional[str] = typer.Option(None, "--session-id"),
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    """Record an automated rectangular FreeCam scan into local 7-second video clips."""
    cfg = _config(config)
    from .scan_recorder import build_scan_plan, run_region_scan

    plan = build_scan_plan(
        x_min=x_min,
        x_max=x_max,
        z_min=z_min,
        z_max=z_max,
        y=y,
        points_x=points_x,
        points_z=points_z,
        yaw_step_deg=yaw_step,
        pitch_deg=pitch_deg,
        fov=fov,
    )
    console.print(
        f"[yellow]Scan will record {len(plan)} clips, about {len(plan) * segment_seconds / 60:.1f} minutes "
        "plus OBS overhead.[/yellow]"
    )
    outputs = run_region_scan(
        cfg,
        obs_password=obs_password,
        x_min=x_min,
        x_max=x_max,
        z_min=z_min,
        z_max=z_max,
        y=y,
        points_x=points_x,
        points_z=points_z,
        yaw_step_deg=yaw_step,
        segment_seconds=segment_seconds,
        settle_seconds=settle_seconds,
        post_stop_seconds=post_stop_seconds,
        pitch_deg=pitch_deg,
        fov=fov,
        max_segments=max_segments,
        start_segment=start_segment,
        session_id=session_id,
    )
    table = Table(title="Scan outputs")
    table.add_column("Name")
    table.add_column("Path")
    for name, path in outputs.items():
        table.add_row(name, str(path))
    console.print(table)


@app.command("scan-monitor")
def scan_monitor(
    session_id: Optional[str] = typer.Option(None, "--session-id"),
    total_segments: int = typer.Option(180, "--total-segments"),
    refresh_sec: float = typer.Option(2.0, "--refresh-sec"),
    topmost: bool = typer.Option(True, "--topmost/--no-topmost"),
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    """Open a live progress monitor for scan-region outputs."""
    cfg = _config(config)
    from .scan_monitor import run_scan_monitor

    run_scan_monitor(
        cfg,
        session_id=session_id,
        total_segments=total_segments,
        refresh_sec=refresh_sec,
        topmost=topmost,
    )


@app.command("scan-stills")
def scan_stills(
    x_min: float = typer.Option(22.63, "--x-min"),
    x_max: float = typer.Option(153.83, "--x-max"),
    z_min: float = typer.Option(-6.16, "--z-min"),
    z_max: float = typer.Option(11.49, "--z-max"),
    y_values: str = typer.Option("9.41,10.10,10.78", "--y-values"),
    obs_password: str = typer.Option("", "--obs-password"),
    points_x: int = typer.Option(5, "--points-x"),
    points_z: int = typer.Option(3, "--points-z"),
    settle_seconds: float = typer.Option(0.6, "--settle-seconds"),
    source_name: Optional[str] = typer.Option(None, "--source-name"),
    image_format: str = typer.Option("jpg", "--image-format"),
    image_width: int = typer.Option(1920, "--image-width"),
    image_height: int = typer.Option(1080, "--image-height"),
    image_quality: int = typer.Option(100, "--image-quality"),
    max_samples: Optional[int] = typer.Option(None, "--max-samples"),
    session_id: Optional[str] = typer.Option(None, "--session-id"),
    layers_config: Optional[Path] = typer.Option(None, "--layers-config"),
    pose_plan_config: Optional[Path] = typer.Option(None, "--pose-plan-config"),
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    """Capture OBS stills on a grid using the 22-view pitch/yaw sampling pattern."""
    cfg = _config(config)
    from .still_scan import (
        build_layered_still_scan_plan,
        build_still_scan_plan,
        load_still_layers,
        load_still_pose_plan,
        parse_float_list,
        run_layered_still_scan,
        run_still_pose_plan,
        run_still_scan,
    )

    if pose_plan_config is not None:
        plan = load_still_pose_plan(pose_plan_config, group_id=session_id or "scene_1_extra")
        layer_count = len({sample.layer_id for sample in plan})
    elif layers_config is not None:
        layers = load_still_layers(layers_config)
        plan = build_layered_still_scan_plan(layers, points_x=points_x, points_z=points_z)
        layer_count = len(layers)
    else:
        heights = parse_float_list(y_values)
        plan = build_still_scan_plan(
            x_min=x_min,
            x_max=x_max,
            z_min=z_min,
            z_max=z_max,
            y_values=heights,
            points_x=points_x,
            points_z=points_z,
        )
        layer_count = len(heights)
    planned_count = len(plan) if max_samples is None else min(len(plan), max_samples)
    if pose_plan_config is not None:
        console.print(
            f"[yellow]Still scan will capture {planned_count} explicit pose images "
            f"({layer_count} subscenes, 1 view per JSON sample).[/yellow]"
        )
        outputs = run_still_pose_plan(
            cfg,
            obs_password=obs_password,
            pose_plan=pose_plan_config,
            settle_seconds=settle_seconds,
            source_name=source_name,
            image_format=image_format,
            image_width=image_width,
            image_height=image_height,
            image_quality=image_quality,
            session_id=session_id,
            max_samples=max_samples,
        )
    elif layers_config is not None:
        console.print(
            f"[yellow]Still scan will capture {planned_count} images "
            f"({points_x * points_z} x/z points per layer, {layer_count} layers, 22 views per point).[/yellow]"
        )
        outputs = run_layered_still_scan(
            cfg,
            obs_password=obs_password,
            layers=layers,
            points_x=points_x,
            points_z=points_z,
            settle_seconds=settle_seconds,
            source_name=source_name,
            image_format=image_format,
            image_width=image_width,
            image_height=image_height,
            image_quality=image_quality,
            session_id=session_id,
            max_samples=max_samples,
        )
    else:
        console.print(
            f"[yellow]Still scan will capture {planned_count} images "
            f"({points_x * points_z} x/z points per layer, {layer_count} layers, 22 views per point).[/yellow]"
        )
        outputs = run_still_scan(
            cfg,
            obs_password=obs_password,
            x_min=x_min,
            x_max=x_max,
            z_min=z_min,
            z_max=z_max,
            y_values=heights,
            points_x=points_x,
            points_z=points_z,
            settle_seconds=settle_seconds,
            source_name=source_name,
            image_format=image_format,
            image_width=image_width,
            image_height=image_height,
            image_quality=image_quality,
            session_id=session_id,
            max_samples=max_samples,
        )
    table = Table(title="Still scan outputs")
    table.add_column("Name")
    table.add_column("Path")
    for name, path in outputs.items():
        table.add_row(name, str(path))
    console.print(table)


@app.command("scan-stills-gui")
def scan_stills_gui(
    x_min: float = typer.Option(22.63, "--x-min"),
    x_max: float = typer.Option(153.83, "--x-max"),
    z_min: float = typer.Option(-6.16, "--z-min"),
    z_max: float = typer.Option(11.49, "--z-max"),
    y_values: str = typer.Option("9.41,10.10,10.78", "--y-values"),
    obs_password: str = typer.Option("", "--obs-password"),
    points_x: int = typer.Option(5, "--points-x"),
    points_z: int = typer.Option(3, "--points-z"),
    settle_seconds: float = typer.Option(0.6, "--settle-seconds"),
    source_name: Optional[str] = typer.Option(None, "--source-name"),
    image_format: str = typer.Option("jpg", "--image-format"),
    image_width: int = typer.Option(1920, "--image-width"),
    image_height: int = typer.Option(1080, "--image-height"),
    image_quality: int = typer.Option(100, "--image-quality"),
    max_samples: Optional[int] = typer.Option(None, "--max-samples"),
    session_id: Optional[str] = typer.Option(None, "--session-id"),
    layers_config: Optional[Path] = typer.Option(None, "--layers-config"),
    resume_from_layer: Optional[str] = typer.Option(None, "--resume-from-layer"),
    pose_plan_config: Optional[Path] = typer.Option(None, "--pose-plan-config"),
    trajectory_set: str = typer.Option("scene_1_1_true_gain2_smoke10", "--trajectory-set"),
    trajectory_json: Optional[Path] = typer.Option(None, "--trajectory-json"),
    trajectory_output_dir: Optional[Path] = typer.Option(None, "--trajectory-output-dir"),
    trajectory_label: Optional[str] = typer.Option(None, "--trajectory-label"),
    trajectory_session_prefix: Optional[str] = typer.Option(None, "--trajectory-session-prefix"),
    topmost: bool = typer.Option(True, "--topmost/--no-topmost"),
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    """Open a progress UI for the 22-view OBS still-image grid scan."""
    cfg = _config(config)
    from .still_scan_gui import run_still_scan_gui

    run_still_scan_gui(
        cfg,
        obs_password=obs_password,
        x_min=x_min,
        x_max=x_max,
        z_min=z_min,
        z_max=z_max,
        y_values=y_values,
        points_x=points_x,
        points_z=points_z,
        settle_seconds=settle_seconds,
        source_name=source_name,
        image_format=image_format,
        image_width=image_width,
        image_height=image_height,
        image_quality=image_quality,
        max_samples=max_samples,
        session_id=session_id,
        layers_config=layers_config,
        resume_from_layer=resume_from_layer,
        pose_plan_config=pose_plan_config,
        trajectory_set_id=trajectory_set,
        trajectory_json=trajectory_json,
        trajectory_output_dir=trajectory_output_dir,
        trajectory_label=trajectory_label,
        trajectory_session_prefix=trajectory_session_prefix,
        topmost=topmost,
    )


@app.command("detect-inaccessible-points")
def detect_inaccessible_points_command(
    samples: Path = typer.Option(..., "--samples"),
    out: Optional[Path] = typer.Option(None, "--out"),
    entropy_threshold: float = typer.Option(3.0, "--entropy-threshold"),
    std_threshold: float = typer.Option(3.0, "--std-threshold"),
    edge_density_threshold: float = typer.Option(0.004, "--edge-density-threshold"),
    dark_ratio_threshold: float = typer.Option(0.98, "--dark-ratio-threshold"),
    bright_ratio_threshold: float = typer.Option(0.995, "--bright-ratio-threshold"),
    delete_images: bool = typer.Option(False, "--delete-images/--keep-images"),
) -> None:
    """After a scan finishes, flag bad stills and optionally delete whole bad camera points."""
    from .bad_still_detector import detect_inaccessible_points

    outputs = detect_inaccessible_points(
        samples,
        output_dir=out,
        entropy_threshold=entropy_threshold,
        std_threshold=std_threshold,
        edge_density_threshold=edge_density_threshold,
        dark_ratio_threshold=dark_ratio_threshold,
        bright_ratio_threshold=bright_ratio_threshold,
        delete_invalid_images=delete_images,
    )
    table = Table(title="Inaccessible point QA outputs")
    table.add_column("Name")
    table.add_column("Path")
    for name, path in outputs.items():
        table.add_row(name, str(path))
    console.print(table)


@app.command("warmup-laion")
def warmup_laion(
    model: Optional[str] = typer.Option(None, "--model"),
    device: Optional[str] = typer.Option(None, "--device"),
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    """Download/cache OpenCLIP weights and load the LAION aesthetic head once."""
    cfg = _config(config)
    from .laion_scorer import LAIONAestheticScorer

    scorer = LAIONAestheticScorer(
        model_name=model or cfg.raw["laion"]["model"],
        device=device or cfg.raw["laion"]["device"],
        repo_dir=cfg.laion_repo_dir,
        cache_dir=cfg.raw["laion"]["cache_dir"],
        hf_cache_dir=cfg.raw["laion"].get("hf_cache_dir", "third_party/huggingface_cache"),
    ).load_model()
    console.print(f"[green]LAION scorer ready:[/green] model={scorer.model_name} device={scorer.device_name}")


@app.command("extract-frames")
def extract_frames(
    video: Path = typer.Option(..., "--video"),
    out: Path = typer.Option(..., "--out"),
    fps: float = typer.Option(2.0, "--fps"),
    jpeg_quality: int = typer.Option(95, "--jpeg-quality"),
    overwrite: bool = typer.Option(False, "--overwrite"),
) -> None:
    rows = extract_video_frames(video, out, fps, jpeg_quality=jpeg_quality, overwrite=overwrite)
    console.print(f"[green]Extracted {len(rows)} frames:[/green] {out}")


@app.command("score-frames")
def score_frames(
    input: Path = typer.Option(..., "--input"),
    output: Path = typer.Option(..., "--output"),
    device: str = typer.Option("auto", "--device"),
    batch_size: int = typer.Option(32, "--batch-size"),
    model: str = typer.Option("vit_l_14", "--model"),
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    cfg = _config(config)
    from .laion_scorer import score_folder

    result = score_folder(
        input,
        output,
        model_name=model,
        device=device,
        batch_size=batch_size,
        repo_dir=cfg.laion_repo_dir,
        cache_dir=cfg.raw["laion"]["cache_dir"],
        hf_cache_dir=cfg.raw["laion"].get("hf_cache_dir", "third_party/huggingface_cache"),
    )
    console.print(f"[green]Wrote {len(result)} scores:[/green] {output}")


@app.command("align-pose")
def align_pose(
    scores: Path = typer.Option(..., "--scores"),
    pose_log: Path = typer.Option(..., "--pose-log"),
    out: Path = typer.Option(..., "--out"),
    method: str = typer.Option("nearest", "--method"),
    max_time_diff_sec: float = typer.Option(0.25, "--max-time-diff-sec"),
) -> None:
    result = align_pose_file(scores, pose_log, out, method=method, max_time_diff_sec=max_time_diff_sec)
    console.print(f"[green]Aligned {len(result)} rows:[/green] {out}")


@app.command("build-trajectory")
def build_trajectory(
    scores_with_pose: Path = typer.Option(..., "--scores-with-pose"),
    out: Path = typer.Option(Path("outputs/trajectory_to_best.csv"), "--out"),
    plot: Path = typer.Option(Path("outputs/trajectory_to_best.png"), "--plot"),
    start_mode: str = typer.Option("first", "--start-mode"),
    max_step_distance: Optional[float] = typer.Option(None, "--max-step-distance"),
    neighbor_count: int = typer.Option(50, "--neighbor-count"),
) -> None:
    """Build a score-ascent trajectory through sampled poses."""
    from .trajectory import build_score_ascent_trajectory

    result = build_score_ascent_trajectory(
        scores_with_pose,
        output_csv=out,
        output_plot=plot,
        start_mode=start_mode,
        max_step_distance=max_step_distance,
        neighbor_count=neighbor_count,
    )
    best = result.iloc[-1]
    console.print(f"[green]Wrote trajectory rows:[/green] {len(result)} -> {out}")
    console.print(f"[green]Plot:[/green] {plot}")
    console.print(
        f"Best sampled pose: score={float(best['score']):.3f}, "
        f"x={float(best['x']):.3f}, y={float(best['y']):.3f}, z={float(best['z']):.3f}"
    )


@app.command("analyze-stutter")
def analyze_stutter(
    video: Path = typer.Option(..., "--video"),
    pose_log: Optional[Path] = typer.Option(None, "--pose-log"),
    out: Optional[Path] = typer.Option(None, "--out"),
) -> None:
    """Estimate video dropped-frame/stutter artifacts and optional pose smoothness."""
    from .stutter_analyzer import analyze_pose_smoothness, analyze_video_stutter

    video_report = analyze_video_stutter(video, output_json=out)
    table = Table(title="Video stutter metrics")
    table.add_column("Metric")
    table.add_column("Value")
    for key, value in video_report.to_dict().items():
        table.add_row(key, f"{value}")
    console.print(table)
    if pose_log is not None:
        pose_out = out.with_name(out.stem + "_pose.json") if out is not None else None
        pose_report = analyze_pose_smoothness(pose_log, output_json=pose_out)
        pose_table = Table(title="Pose smoothness metrics")
        pose_table.add_column("Metric")
        pose_table.add_column("Value")
        for key, value in pose_report.items():
            pose_table.add_row(key, f"{value}")
        console.print(pose_table)


@app.command("replay-trajectory")
def replay_trajectory(
    trajectory_json: Path = typer.Option(..., "--trajectory-json"),
    trajectory_id: Optional[str] = typer.Option(None, "--trajectory-id"),
    trajectory_index: int = typer.Option(1, "--trajectory-index"),
    obs_password: str = typer.Option("", "--obs-password"),
    output_dir: Optional[Path] = typer.Option(None, "--output-dir"),
    session_id: Optional[str] = typer.Option(None, "--session-id"),
    angle_unit: str = typer.Option("auto", "--angle-unit"),
    keyframe_interval_sec: float = typer.Option(0.2, "--keyframe-interval-sec"),
    countdown_sec: float = typer.Option(3.0, "--countdown-sec"),
    settle_sec: float = typer.Option(1.0, "--settle-sec"),
    post_roll_sec: float = typer.Option(1.0, "--post-roll-sec"),
    speed: float = typer.Option(1.0, "--speed"),
    duration_sec: Optional[float] = typer.Option(None, "--duration-sec"),
    record: bool = typer.Option(True, "--record/--no-record"),
    write_pose_log: bool = typer.Option(True, "--pose-log/--no-pose-log"),
    unwrap_yaw: bool = typer.Option(True, "--unwrap-yaw/--no-unwrap-yaw"),
    reverse: bool = typer.Option(False, "--reverse/--forward"),
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    """Replay one trajectory JSON locally through FreeCam and OBS recording."""
    cfg = _config(config)
    from .trajectory_replay import load_replay_trajectory, replay_trajectory_to_obs

    trajectory = load_replay_trajectory(
        trajectory_json,
        trajectory_id=trajectory_id,
        trajectory_index=trajectory_index,
        angle_unit=angle_unit,
        keyframe_interval_sec=keyframe_interval_sec,
        unwrap_yaw=unwrap_yaw,
        reverse=reverse,
    )
    console.print(
        f"[green]Loaded trajectory:[/green] {trajectory.trajectory_id} "
        f"keyframes={len(trajectory.keyframes)} duration={trajectory.duration_sec:.2f}s"
    )
    console.print("[yellow]Make sure the game is open, FreeCam is enabled, and OBS WebSocket is connected.[/yellow]")
    outputs = replay_trajectory_to_obs(
        cfg,
        trajectory,
        obs_password=obs_password,
        output_dir=output_dir,
        session_id=session_id,
        countdown_sec=countdown_sec,
        settle_sec=settle_sec,
        post_roll_sec=post_roll_sec,
        speed=speed,
        duration_sec=duration_sec,
        record=record,
        write_pose_log=write_pose_log,
    )
    table = Table(title="Trajectory replay outputs")
    table.add_column("Name")
    table.add_column("Path")
    for name, path in outputs.items():
        table.add_row(name, str(path))
    console.print(table)


@app.command("analyze-video")
def analyze_video(
    video: Path = typer.Option(..., "--video"),
    pose_log: Path = typer.Option(..., "--pose-log"),
    fps: float = typer.Option(2.0, "--fps"),
    device: str = typer.Option("auto", "--device"),
    top_k: int = typer.Option(50, "--top-k"),
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    cfg = _config(config)
    _analyze_video_pipeline(cfg, video, pose_log, fps=fps, device=device, top_k=top_k, session_id=video.stem)


@app.command("record-and-score")
def record_and_score(
    obs_password: str = typer.Option("", "--obs-password"),
    fps: Optional[float] = typer.Option(None, "--fps"),
    device: Optional[str] = typer.Option(None, "--device"),
    top_k: Optional[int] = typer.Option(None, "--top-k"),
    auto_setup_laion: bool = typer.Option(False, "--auto-setup-laion"),
    config: Optional[Path] = typer.Option(None, "--config"),
) -> None:
    cfg = _config(config)
    status = verify_lua_patch_file(cfg)
    if not status.exists:
        console.print(f"[red]Lua path is wrong:[/red] {status.lua_path}")
        raise typer.Exit(1)
    if not status.patched:
        console.print("[red]Lua logger patch is missing.[/red] Run: python -m re9_pose_recorder.cli patch-lua-logger")
        raise typer.Exit(1)
    try:
        verify_laion_repo(cfg.laion_repo_dir)
    except Exception as exc:
        if not auto_setup_laion:
            console.print(f"[red]LAION repo is not ready:[/red] {exc}")
            console.print("Run: python -m re9_pose_recorder.cli setup-laion")
            raise typer.Exit(1)
        ensure_laion_repo(cfg.raw["laion"]["repo_url"], cfg.laion_repo_dir)

    video_path, pose_log, session_id = _record_only(cfg, obs_password)
    _analyze_video_pipeline(
        cfg,
        video_path=video_path,
        pose_log=pose_log,
        fps=fps or float(cfg.raw["video"]["fps"]),
        device=device or cfg.raw["laion"]["device"],
        top_k=top_k or int(cfg.raw["report"]["top_k"]),
        session_id=session_id,
    )


def _record_only(cfg: AppConfig, obs_password: str) -> tuple[Path, Path, str]:
    obs_cfg = cfg.raw["obs"]
    password = obs_password or obs_cfg.get("password", "")
    controller = OBSController(obs_cfg["host"], int(obs_cfg["port"]), password)
    record_dir = ensure_dir(cfg.obs_recording_output_dir)
    try:
        controller.set_record_directory(record_dir)
        console.print(f"[green]OBS recording directory:[/green] {record_dir}")
    except Exception as exc:
        console.print(f"[yellow]Could not set OBS recording directory via WebSocket:[/yellow] {exc}")
        console.print(f"[yellow]Will still look for videos in configured directory:[/yellow] {record_dir}")
    session_id = make_session_id()
    pose_log = _pose_log_for_session(cfg, session_id)
    control = LuaControl(cfg)
    control.write_start_control(session_id, pose_log, float(cfg.raw["lua_logger"]["default_interval_sec"]))
    if not control.wait_until_lua_logging_started(session_id, timeout_sec=5):
        console.print("[yellow]Lua status did not confirm logging within 5 seconds. Continuing anyway.[/yellow]")

    input("Press Enter to start OBS recording...")
    started_at = time.time()
    controller.start_recording()
    console.print("[green]OBS recording started. Manually fly the FreeCam now.[/green]")
    input("Press Enter to stop OBS recording...")
    output_path = controller.stop_recording()
    control.write_stop_control(session_id)
    control.wait_until_lua_logging_stopped(session_id, timeout_sec=5)

    video_path: Path | None = Path(output_path) if output_path else None
    if video_path is None or not video_path.exists():
        configured_dir = obs_cfg.get("recording_output_dir") or ""
        record_dir = cfg.obs_recording_output_dir if configured_dir else controller.get_record_directory()
        if not record_dir:
            raise RuntimeError("OBS did not return an output path and no recording directory is configured.")
        video_path = find_latest_video_file(record_dir, before_time=started_at, supported_extensions=cfg.supported_video_extensions)
    if video_path is None:
        raise RuntimeError("Could not locate the OBS recording. Set obs.recording_output_dir in configs/default.yaml.")
    return video_path, pose_log, session_id


def _analyze_video_pipeline(
    cfg: AppConfig,
    video_path: Path,
    pose_log: Path,
    fps: float,
    device: str,
    top_k: int,
    session_id: str,
) -> None:
    overwrite = bool(cfg.raw["video"].get("overwrite", False))
    output_dir = _output_dir(cfg, session_id, overwrite=overwrite)
    frame_dir = resolve_project_path("data/frames") / session_id
    scores_csv = output_dir / "scores.csv"
    aligned_csv = output_dir / "scores_with_pose.csv"

    extract_video_frames(
        video_path,
        frame_dir,
        target_fps=fps,
        jpeg_quality=int(cfg.raw["video"]["jpeg_quality"]),
        overwrite=overwrite,
    )

    from .laion_scorer import score_folder

    score_folder(
        frame_dir,
        scores_csv,
        frame_metadata_csv=frame_dir / "frame_metadata.csv",
        model_name=cfg.raw["laion"]["model"],
        device=device,
        batch_size=int(cfg.raw["laion"]["batch_size"]),
        repo_dir=cfg.laion_repo_dir,
        cache_dir=cfg.raw["laion"]["cache_dir"],
        hf_cache_dir=cfg.raw["laion"].get("hf_cache_dir", "third_party/huggingface_cache"),
    )

    pose_copy_for_data = None
    pose_copy_for_outputs = None
    if pose_log.exists():
        data_pose_dir = ensure_dir("data/pose_logs")
        pose_copy_for_data = data_pose_dir / f"{pose_log.stem}.csv"
        shutil.copy2(pose_log, pose_copy_for_data)
        pose_copy_for_outputs = output_dir / "pose_log.csv"
        shutil.copy2(pose_log, pose_copy_for_outputs)
        align_pose_file(
            scores_csv,
            pose_copy_for_outputs,
            aligned_csv,
            method=cfg.raw["alignment"]["method"],
            max_time_diff_sec=float(cfg.raw["alignment"]["max_time_diff_sec"]),
        )
    else:
        console.print(f"[yellow]Pose log missing: {pose_log}. Writing score-only aligned CSV.[/yellow]")
        import pandas as pd

        scores = pd.read_csv(scores_csv)
        scores["alignment_valid"] = False
        scores.to_csv(aligned_csv, index=False)

    generate_report(
        aligned_csv,
        output_dir,
        top_k=top_k,
        copy_top_frames=bool(cfg.raw["report"]["copy_top_frames"]),
        smooth_window=int(cfg.raw["report"]["smooth_window"]),
        session_id=session_id,
        extraction_fps=fps,
        pose_log_csv=pose_copy_for_outputs or pose_copy_for_data,
    )
    _print_outputs(output_dir)


def _print_outputs(output_dir: Path) -> None:
    table = Table(title="Generated outputs")
    table.add_column("File")
    table.add_column("Path")
    for name in ["scores.csv", "pose_log.csv", "scores_with_pose.csv", "score_curve.png", "camera_path.png", "report.html"]:
        path = output_dir / name
        table.add_row(name, str(path) if path.exists() else "(not created)")
    table.add_row("top_frames", str(output_dir / "top_frames"))
    console.print(table)


def main() -> None:
    setup_logging()
    app()


if __name__ == "__main__":
    main()
