"""Per-call render variables derived from domain values — pure, no I/O."""

from kodezart.types.domain.consolidation import ChangesetDigest


def changeset_variables(changeset: ChangesetDigest) -> dict[str, object]:
    """Render variables for the changeset section of an evaluation template.

    The empty / non-empty split and the "(none)" file-list case are expressed
    as PRESENCE of a variable, matching the renderer's presence-conditional
    ``{{#if}}``: a name is bound only when its section applies.
    """
    variables: dict[str, object] = {
        "commit_count": changeset.commit_count,
        "file_paths": changeset.file_paths,
        "commit_subjects": changeset.commit_subjects,
    }
    if changeset.is_empty:
        variables["changeset_is_empty"] = True
    else:
        variables["changeset_has_commits"] = True
    if not changeset.file_paths:
        variables["file_paths_absent"] = True
    return variables
