from pathlib import Path

from scripts import run_extract


def test_position_classification_is_enabled_and_inherits_workers() -> None:
    args = run_extract.build_parser().parse_args(
        [
            "--input",
            "input.xlsx",
            "--max-workers",
            "50",
        ]
    )

    assert args.skip_position_classification is False
    assert args.position_batch_size == 1
    assert args.position_max_workers is None


def test_run_position_classification_targets_only_current_run(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls = []
    monkeypatch.setattr(
        run_extract,
        "classify_positions",
        lambda args: calls.append(("classify", args)),
    )
    monkeypatch.setattr(
        run_extract,
        "apply_position_classification",
        lambda args: calls.append(("apply", args)),
    )
    args = run_extract.build_parser().parse_args(
        [
            "--input",
            "input.xlsx",
            "--output",
            str(tmp_path),
            "--max-workers",
            "50",
        ]
    )
    run_dir = tmp_path / "runs" / "sample"

    output = run_extract.run_position_classification(run_dir, args)

    assert [name for name, _ in calls] == ["classify", "apply"]
    position_args = calls[0][1]
    assert position_args.run == [run_dir]
    assert position_args.batch_size == 1
    assert position_args.max_workers == 50
    assert position_args.checkpoint.name == "sample.checkpoint.json"
    assert output == tmp_path / "runs_position_v3" / "sample_position_v3"
