"""Installing and removing completion links (ADR-0066, CONTRACT.md rule 1)."""

from pathlib import Path

import pytest

from maniac import manifest
from maniac.completion_files import (
    CompletionTaken,
    install_completion,
    link_path,
    remove_completions,
)
from maniac.config import Config

_ZSH = "#compdef tool\n_tool() { }\n"


@pytest.fixture
def cfg(tmp_path: Path) -> Config:
    return Config(
        manifest_path=tmp_path / "state" / "installed.json",
        completions_output_dir=tmp_path / "data" / "maniac" / "completions",
        completion_dirs={
            "bash": tmp_path / "data" / "bash-completion" / "completions",
            "zsh": tmp_path / "data" / "zsh" / "site-functions",
            "fish": tmp_path / "data" / "fish" / "vendor_completions.d",
        },
    )


def test_a_generated_completion_is_saved_and_linked(cfg: Config) -> None:
    with manifest.transaction(cfg) as txn:
        entry = install_completion(
            "tool",
            "zsh",
            script=_ZSH,
            arguments=("completion", "zsh"),
            version="1.0",
            txn=txn,
        )

    link = link_path("zsh", "tool", cfg)
    assert link.name == "_tool"
    assert link.resolve() == entry.target
    assert entry.target.read_text(encoding="utf-8") == _ZSH
    assert entry.target.is_relative_to(cfg.completions_output_dir)
    assert manifest.completions_of("tool", cfg)["zsh"].arguments == (
        "completion",
        "zsh",
    )


def test_a_shipped_completion_links_to_the_providers_file(
    cfg: Config, tmp_path: Path
) -> None:
    shipped = tmp_path / "root" / "completions" / "tool.fish"
    shipped.parent.mkdir(parents=True)
    shipped.write_text("complete -c tool\n", encoding="utf-8")

    with manifest.transaction(cfg) as txn:
        entry = install_completion(
            "tool", "fish", shipped=shipped, version="1.0", txn=txn
        )

    assert link_path("fish", "tool", cfg).resolve() == shipped
    assert entry.provider_target


def test_a_completion_maniac_did_not_install_is_never_replaced(cfg: Config) -> None:
    theirs = link_path("bash", "tool", cfg)
    theirs.parent.mkdir(parents=True)
    theirs.write_text("complete -F _mine tool\n", encoding="utf-8")

    with (
        pytest.raises(CompletionTaken),
        manifest.transaction(cfg) as txn,
    ):
        install_completion(
            "tool",
            "bash",
            script="complete -F _tool tool\n",
            arguments=("completion", "bash"),
            version="1.0",
            txn=txn,
        )

    assert theirs.read_text(encoding="utf-8") == "complete -F _mine tool\n"


def test_reinstalling_replaces_maniacs_own_link(cfg: Config) -> None:
    for version in ("1.0", "2.0"):
        with manifest.transaction(cfg) as txn:
            install_completion(
                "tool",
                "zsh",
                script=f"#compdef tool\n# {version}\n",
                arguments=("c", "zsh"),
                version=version,
                txn=txn,
            )

    assert manifest.completions_of("tool", cfg)["zsh"].version == "2.0"
    assert "2.0" in link_path("zsh", "tool", cfg).read_text(encoding="utf-8")


def test_removing_unlinks_and_deletes_only_maniacs_copies(
    cfg: Config, tmp_path: Path
) -> None:
    shipped = tmp_path / "root" / "tool.fish"
    shipped.parent.mkdir(parents=True)
    shipped.write_text("complete -c tool\n", encoding="utf-8")
    with manifest.transaction(cfg) as txn:
        generated = install_completion(
            "tool", "zsh", script=_ZSH, arguments=("c",), version=None, txn=txn
        )
        install_completion("tool", "fish", shipped=shipped, version=None, txn=txn)

    with manifest.transaction(cfg) as txn:
        removed = remove_completions("tool", txn)

    assert removed.removed == ("fish", "zsh")
    assert not link_path("zsh", "tool", cfg).is_symlink()
    assert not generated.target.exists()
    assert shipped.exists()
    assert manifest.completions_of("tool", cfg) == {}


def test_a_link_someone_repointed_is_kept_and_reported(
    cfg: Config, tmp_path: Path
) -> None:
    with manifest.transaction(cfg) as txn:
        install_completion(
            "tool", "zsh", script=_ZSH, arguments=("c",), version=None, txn=txn
        )
    link = link_path("zsh", "tool", cfg)
    mine = tmp_path / "mine"
    mine.write_text(_ZSH, encoding="utf-8")
    link.unlink()
    link.symlink_to(mine)

    with manifest.transaction(cfg) as txn:
        removed = remove_completions("tool", txn)

    assert removed.kept == (link,)
    assert link.resolve() == mine
