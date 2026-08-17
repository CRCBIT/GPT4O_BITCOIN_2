#!/usr/bin/env python3
"""메모리 주식 모델을 로컬에서 계산하고 Streamlit Cloud 브랜치로 게시한다.

무거운 yfinance 수집·워크포워드·앙상블 학습은 이 파일을 실행하는 컴퓨터에서만
수행한다. 결과는 pickle이 아닌 CSV/JSON으로 직렬화하고, 임시 clone에서 지정된
파일만 커밋하므로 사용자의 현재 Git 작업트리를 변경하지 않는다.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

import memory_stock_predict_pro as engine

try:
    import fcntl
except ImportError:  # pragma: no cover - Ubuntu에서는 항상 사용 가능
    fcntl = None


PROJECT_DIR = Path(__file__).resolve().parent
CLOUD_DIR = PROJECT_DIR / "cloud"
DEFAULT_REPO = "git@github.com:CRCBIT/GPT4O_BITCOIN_2.git"
DEFAULT_BRANCH = "main"
SCHEMA_VERSION = 1


def log(message: str) -> None:
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    print(f"[{stamp}] {message}", flush=True)


def run_command(args: list[str], cwd: Path | None = None,
                check: bool = True) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    result = subprocess.run(
        args, cwd=str(cwd) if cwd else None, env=env,
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"명령 실패({result.returncode}): {' '.join(args)}\n{detail}")
    return result


def clean_json(value: Any) -> Any:
    """numpy/pandas/NaN을 엄격한 JSON 값으로 변환한다."""
    if value is None or value is pd.NA:
        return None
    if isinstance(value, dict):
        return {str(k): clean_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, np.ndarray)):
        return [clean_json(v) for v in value]
    if isinstance(value, (pd.Timestamp, datetime)):
        if pd.isna(value):
            return None
        return pd.Timestamp(value).isoformat()
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if pd.isna(value):
        return None
    return value


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(clean_json(payload), ensure_ascii=False, indent=2,
                   sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_ticker_name(ticker: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", ticker).strip("_") or "ticker"


def frame_to_csv(frame: pd.DataFrame, path: Path, *, index: bool = False,
                 compression: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=index, encoding="utf-8", compression=compression)


def metrics_summary(metrics: dict | None) -> dict:
    if not metrics:
        return {}
    return {
        "overall": metrics.get("overall"),
        "base": metrics.get("base"),
        "naive": metrics.get("naive"),
        "n": metrics.get("n"),
        "accuracy_ci": metrics.get("accuracy_ci"),
        "brier": metrics.get("brier"),
        "brier_base": metrics.get("brier_base"),
        "brier_skill": metrics.get("brier_skill"),
        "log_loss": metrics.get("log_loss"),
        "auc": metrics.get("auc"),
        "ece": metrics.get("ece"),
        "prec_up": metrics.get("prec_up"),
        "n_up": metrics.get("n_up"),
        "prec_dn": metrics.get("prec_dn"),
        "n_dn": metrics.get("n_dn"),
    }


def latest_quote_payload(prices: dict[str, pd.DataFrame]) -> dict:
    payload = {}
    for ticker, frame in prices.items():
        if frame.empty or "Close" not in frame:
            continue
        close = pd.to_numeric(frame["Close"], errors="coerce").dropna()
        if close.empty:
            continue
        payload[ticker] = {
            "date": close.index[-1],
            "close": close.iloc[-1],
            "previous_close": close.iloc[-2] if len(close) >= 2 else None,
        }
    return payload


def build_bundle(out: dict, destination: Path, *, period: str, horizon: int,
                 threshold: int, cost_bps: int) -> dict:
    """학습 결과를 Cloud가 sklearn 없이 읽을 수 있는 데이터 계약으로 만든다."""
    destination.mkdir(parents=True, exist_ok=True)
    prices = out["prices"]
    oos = out["oos"].copy()
    scores = out["scores"].copy()
    metrics = engine.compute_metrics(oos, thr=threshold, horizon=horizon)
    reliability = engine.reliability_summary(metrics)

    plans: dict[str, dict] = {}
    board_rows = []
    score_map = (scores.set_index("ticker") if not scores.empty
                 else pd.DataFrame())
    for ticker in engine.TICKERS:
        if ticker not in prices or scores.empty or ticker not in score_map.index:
            continue
        score = float(score_map.loc[ticker, "score"])
        evidence = engine.score_evidence(oos, ticker, score, horizon=horizon)
        plan = engine.make_action_plan(
            prices[ticker], score, out["ret_stats"].get(ticker), horizon,
            threshold, engine.ticker_currency(ticker),
            quality=reliability.get("quality", 0.35), evidence=evidence,
        )
        data_rows = out["data"][out["data"]["ticker"] == ticker].sort_values("date")
        reasons = engine.plain_reasons(ticker, data_rows.iloc[-1]) \
            if len(data_rows) else []
        plan.update({
            "ticker": ticker,
            "name": engine.TICKERS[ticker],
            "date": score_map.loc[ticker, "date"],
            "reasons": reasons,
        })
        plans[ticker] = plan
        ev = plan.get("evidence", {})
        board_rows.append({
            "ticker": ticker,
            "종목": engine.TICKERS[ticker],
            "행동": f"{plan['emoji']} {plan['label']}",
            "상승확률": plan["score"] / 100.0,
            "실행점수": plan["decision_score"],
            "유사점수_실제상승률": ev.get("rate"),
            "유사점수_표본수": ev.get("n"),
            "현재가": plan.get("price"),
            "매수기준": plan.get("buy"),
            "목표가": plan.get("target"),
            "예상하단": plan.get("t_lo"),
            "예상상단": plan.get("t_hi"),
            "손절가": plan.get("stop"),
            "예상수익률": plan.get("er"),
            "통화": plan.get("ccy"),
            "핵심근거": " · ".join(reasons) if reasons else plan.get("why_short"),
        })

    frame_to_csv(scores, destination / "scores.csv")
    frame_to_csv(pd.DataFrame(board_rows), destination / "decision_board.csv")
    frame_to_csv(oos, destination / "oos.csv.gz", compression="gzip")
    frame_to_csv(out["spot_data"], destination / "spot_prices.csv")

    importance = out.get("importance")
    imp_frame = (importance.rename("importance").rename_axis("feature").reset_index()
                 if isinstance(importance, pd.Series)
                 else pd.DataFrame(columns=["feature", "importance"]))
    frame_to_csv(imp_frame, destination / "feature_importance.csv")

    if metrics:
        per_ticker = metrics["per_ticker"].rename_axis("ticker").reset_index()
        calibration = metrics["calibration"].reset_index()
        if "bin" in calibration:
            calibration["bin"] = calibration["bin"].astype(str)
        frame_to_csv(per_ticker, destination / "metrics_per_ticker.csv")
        frame_to_csv(calibration, destination / "calibration.csv")
        frame_to_csv(metrics["rolling"], destination / "rolling_accuracy.csv")
    else:
        frame_to_csv(pd.DataFrame(), destination / "metrics_per_ticker.csv")
        frame_to_csv(pd.DataFrame(), destination / "calibration.csv")
        frame_to_csv(pd.DataFrame(), destination / "rolling_accuracy.csv")

    ticker_files = {}
    price_dir = destination / "prices"
    for ticker in engine.TICKERS:
        if ticker not in prices:
            continue
        filename = f"{safe_ticker_name(ticker)}.csv.gz"
        frame = prices[ticker].copy().rename_axis("date").reset_index()
        frame_to_csv(frame, price_dir / filename, compression="gzip")
        ticker_files[ticker] = f"prices/{filename}"

    write_json(destination / "plans.json", plans)
    write_json(destination / "metrics_summary.json", metrics_summary(metrics))
    write_json(destination / "reliability.json", reliability)
    write_json(destination / "model_info.json", out.get("model_info", {}))
    write_json(destination / "ret_stats.json", out.get("ret_stats", {}))
    write_json(destination / "spot_status.json", out.get("spot_status", {}))

    latest_date = None
    if not scores.empty:
        latest_date = pd.to_datetime(scores["date"], errors="coerce").max()
    generated_at = datetime.now(timezone.utc)
    hashes = {
        str(path.relative_to(destination)): sha256_file(path)
        for path in sorted(destination.rglob("*"))
        if path.is_file() and path.name != "manifest.json"
    }
    identity_material = "\n".join(f"{k}:{v}" for k, v in hashes.items())
    generation_id = hashlib.sha256(identity_material.encode("utf-8")).hexdigest()[:20]
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generation_id": generation_id,
        "generated_at_utc": generated_at,
        "generated_at_local": generated_at.astimezone(),
        "engine_version": engine.VERSION,
        "period": period,
        "horizon": horizon,
        "threshold": threshold,
        "cost_bps": cost_bps,
        "latest_market_date": latest_date,
        "feature_count": len(out.get("feat_cols", [])),
        "oos_rows": len(oos),
        "wf_step": out.get("profile", {}).get("wf_step"),
        "ticker_names": engine.TICKERS,
        "ticker_files": ticker_files,
        "latest_quotes": latest_quote_payload(prices),
        "spot_columns": [c for c in out["spot_data"].columns if c != "날짜"],
        "files": hashes,
    }
    write_json(destination / "manifest.json", manifest)
    return clean_json(manifest)


def validate_branch(branch: str) -> None:
    if (not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._/-]*", branch)
            or ".." in branch or branch.endswith("/") or "//" in branch):
        raise ValueError(f"안전하지 않은 Git 브랜치 이름: {branch!r}")


def copy_cloud_payload(bundle_dir: Path, checkout: Path) -> None:
    target_data = checkout / "published_data"
    if target_data.exists():
        shutil.rmtree(target_data)
    shutil.copytree(bundle_dir, target_data)
    shutil.copy2(CLOUD_DIR / "streamlit_app.py", checkout / "streamlit_app.py")
    shutil.copy2(CLOUD_DIR / "requirements.txt", checkout / "requirements.txt")
    config_target = checkout / ".streamlit" / "config.toml"
    config_target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(CLOUD_DIR / ".streamlit" / "config.toml", config_target)


def publish_to_git(bundle_dir: Path, manifest: dict, *, repo_url: str,
                   branch: str, git_name: str, git_email: str) -> str:
    """임시 clone에서만 수정하고 지정 브랜치로 원자적 commit/push."""
    validate_branch(branch)
    remote_check = run_command(
        ["git", "ls-remote", "--exit-code", "--heads", repo_url, branch],
        check=False,
    )
    branch_exists = remote_check.returncode == 0
    if remote_check.returncode not in (0, 2):
        detail = (remote_check.stderr or remote_check.stdout).strip()
        raise RuntimeError(
            "GitHub 접근에 실패했습니다. `gh auth login` 또는 SSH 인증을 먼저 "
            f"설정하세요.\n{detail}"
        )

    with tempfile.TemporaryDirectory(prefix="memory-dashboard-git-") as temp:
        checkout = Path(temp) / "repo"
        clone = ["git", "clone", "--depth", "1"]
        if branch_exists:
            clone += ["--branch", branch]
        clone += [repo_url, str(checkout)]
        run_command(clone)
        if not branch_exists:
            run_command(["git", "switch", "-c", branch], cwd=checkout)

        old_manifest_path = checkout / "published_data" / "manifest.json"
        if old_manifest_path.exists():
            try:
                old_manifest = json.loads(old_manifest_path.read_text(encoding="utf-8"))
                if old_manifest.get("generation_id") == manifest.get("generation_id"):
                    log("시장 데이터/모델 결과가 직전 게시본과 같아 Git push를 생략합니다.")
                    return "unchanged"
            except (OSError, json.JSONDecodeError):
                pass

        copy_cloud_payload(bundle_dir, checkout)
        run_command([
            "git", "add", "--", "streamlit_app.py", "requirements.txt",
            ".streamlit/config.toml", "published_data",
        ], cwd=checkout)
        if run_command(["git", "diff", "--cached", "--quiet"],
                       cwd=checkout, check=False).returncode == 0:
            log("변경 파일이 없어 Git push를 생략합니다.")
            return "unchanged"
        run_command(["git", "config", "user.name", git_name], cwd=checkout)
        run_command(["git", "config", "user.email", git_email], cwd=checkout)
        market_date = manifest.get("latest_market_date") or "unknown-date"
        message = f"data: refresh memory dashboard ({market_date})"
        run_command(["git", "commit", "-m", message], cwd=checkout)
        run_command([
            "git", "push", "origin", f"HEAD:refs/heads/{branch}",
        ], cwd=checkout)
        commit = run_command(
            ["git", "rev-parse", "--short", "HEAD"], cwd=checkout
        ).stdout.strip()
        return commit


@contextmanager
def single_instance_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    try:
        if fcntl is not None:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError("이미 다른 로컬 학습/게시 작업이 실행 중입니다.") from exc
        yield
    finally:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def export_local_copy(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination, dirs_exist_ok=True)


def run_cycle(args: argparse.Namespace) -> str:
    lock_path = Path(args.lock_file).expanduser().resolve()
    with single_instance_lock(lock_path):
        log(f"로컬 모델 계산 시작: {args.period}, {args.horizon}거래일 전망")
        started = time.monotonic()
        result = engine.run_pipeline(
            args.horizon, args.period, spot_data=None,
            refresh_token=int(time.time()), force_spot=args.force_spot,
        )
        with tempfile.TemporaryDirectory(prefix="memory-dashboard-data-") as temp:
            bundle_dir = Path(temp) / "published_data"
            manifest = build_bundle(
                result, bundle_dir, period=args.period, horizon=args.horizon,
                threshold=args.threshold, cost_bps=args.cost_bps,
            )
            export_local_copy(bundle_dir, Path(args.output_dir).expanduser().resolve())
            log(
                f"데이터 묶음 완성: {manifest['generation_id']} · "
                f"OOS {manifest['oos_rows']:,}행 · {time.monotonic()-started:.1f}초"
            )
            if args.no_push:
                return "local-only"
            commit = publish_to_git(
                bundle_dir, manifest, repo_url=args.repo_url,
                branch=args.branch, git_name=args.git_name,
                git_email=args.git_email,
            )
            log(f"Git 게시 완료: {args.branch} · {commit}")
            return commit


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="로컬 모델 계산 결과를 Streamlit Cloud용 Git 브랜치에 게시")
    parser.add_argument("--repo-url", default=DEFAULT_REPO)
    parser.add_argument("--branch", default=DEFAULT_BRANCH)
    parser.add_argument("--period", choices=list(engine.PERIOD_PROFILES), default="10y")
    parser.add_argument("--horizon", type=int, choices=(10, 20, 40), default=20)
    parser.add_argument("--threshold", type=int, choices=range(52, 71), default=55)
    parser.add_argument("--cost-bps", type=int, default=25)
    parser.add_argument("--interval-hours", type=float, default=0.0,
                        help="0이면 한 번만, 양수면 해당 시간마다 계속 실행")
    parser.add_argument("--force-spot", action="store_true",
                        help="현물가 TTL 캐시를 무시하고 즉시 재수집")
    parser.add_argument("--no-push", action="store_true",
                        help="로컬 데이터만 만들고 GitHub에는 게시하지 않음")
    parser.add_argument(
        "--output-dir", default=str(PROJECT_DIR / "last_published_data"))
    parser.add_argument(
        "--lock-file", default="~/.cache/memory-stock-publisher/run.lock")
    parser.add_argument("--git-name", default="Memory Dashboard Bot")
    parser.add_argument(
        "--git-email", default="memory-dashboard-bot@users.noreply.github.com")
    args = parser.parse_args()
    if args.interval_hours < 0:
        parser.error("--interval-hours는 0 이상이어야 합니다.")
    if args.cost_bps < 0 or args.cost_bps > 500:
        parser.error("--cost-bps는 0~500 범위여야 합니다.")
    validate_branch(args.branch)
    return args


def main() -> int:
    args = parse_args()
    while True:
        try:
            run_cycle(args)
        except KeyboardInterrupt:
            log("사용자가 작업을 중단했습니다.")
            return 130
        except Exception as exc:  # 다음 예약 실행은 계속 살아 있어야 한다.
            log(f"실패: {type(exc).__name__}: {exc}")
            if args.interval_hours <= 0:
                raise
        if args.interval_hours <= 0:
            return 0
        wait_seconds = max(60.0, args.interval_hours * 3600.0)
        log(f"다음 계산까지 {wait_seconds/3600:.2f}시간 대기합니다.")
        try:
            time.sleep(wait_seconds)
        except KeyboardInterrupt:
            log("예약 실행을 종료합니다.")
            return 130


if __name__ == "__main__":
    sys.exit(main())