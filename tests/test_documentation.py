from maniac.config import Config
from maniac.models import RepoSource
from maniac.sources.documentation import documentation_source


def test_documentation_source_redirects_only_the_exact_tmux_build_repository() -> None:
    tmux_distribution = RepoSource(
        name="tmux", target="tmux/tmux-builds", is_local=False
    )
    unrelated_distribution = RepoSource(
        name="foo", target="owner/foo-builds", is_local=False
    )

    mapping = Config().documentation_repository_overrides

    assert documentation_source(tmux_distribution, mapping) == RepoSource(
        name="tmux", target="tmux/tmux", is_local=False
    )
    assert (
        documentation_source(unrelated_distribution, mapping) is unrelated_distribution
    )
