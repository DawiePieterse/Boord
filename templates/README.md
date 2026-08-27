# Import templates

Blank templates for the bulk-import screens under **Admin → Master Data**.
Headings only, no example rows - anything in these files would be imported
as real data.

## blocks.csv

A new install has no blocks. Fill this in and import it via
**Admin → Master Data → Blocks → Import**.

| Column | Required | Notes |
|---|---|---|
| `id` | yes | The label the orchard actually uses - `7`, `8a`, `10b`. Short, because it appears on the field capture screen and on picking slips. Rows without an id are skipped. |
| `name` | no | Display name, e.g. `Block 8a-ED`. Falls back to `id` when blank. |
| `variety` | no | Free text - `Mauritius`, `Early Delight`. Used for grouping in reports. |
| `trees` | no | Tree count, whole number. Feeds per-tree yield figures. |
| `hectares` | no | Decimal, e.g. `2.1`. Feeds per-hectare yield figures. |
| `active` | no | `false` hides a block from capture without deleting its history. Defaults to true. |

Import accepts `.csv` and `.xlsx`. Importing again updates blocks with a
matching `id` rather than duplicating them, so it is safe to correct a
mistake and re-import.

An existing farm can also **export** its blocks from the same screen, edit
the file, and import it back - which is usually easier than starting here.
