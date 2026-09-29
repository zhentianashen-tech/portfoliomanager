# Local sequence editor

Run from the project root:

```bash
python3 tools/run_sequence_editor.py
```

The editor opens at `http://127.0.0.1:8765/`. It is bound to localhost and is not part of the public
portfolio build.

Book and Exhibition are independent editable drafts. Book spreads retain two slots and Exhibition
blocks retain five, but any slot can be empty. Photos can be swapped, replaced by dragging directly
from the library onto a slot, or removed directly from a slot and returned to **Unused**. The photo
library labels each photograph as **Book only**, **Exhibition only**, **Book + Exhibition**, or
**Unused in both**, with filters for everything unused in either individual draft. Spreads and blocks
can be added, removed, and reordered, and each has an editable title and comment.

**Save version** writes a complete JSON checkpoint and readable Markdown report under
`analysis/sequence_edits/`, then records that checkpoint as `latest.json`. The latest saved version is
loaded whenever the editor opens, while unsaved work is also kept in browser storage as a safety net.
Older checkpoints remain available in the interface's Version history. Saving does not alter the
canonical sequence or public site. The header's Restore menu can load the published baseline or any
saved checkpoint for review; use **Save version** afterward only if it should become the latest draft.
