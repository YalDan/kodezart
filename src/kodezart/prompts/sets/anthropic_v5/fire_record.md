{{#if records.fire}}Record this fire in {{records.fire.name}} ({{records.fire.system}} data source
{{records.fire.id}}). Before returning from this session, find the row whose
title is exactly:

{{record_title}}

Create that row only if it is absent. Use this exact title for the whole fire,
including later iterations and repair sessions. In the {{records.fire.columns.what_happened}}
property, append a concise, honest account of what you observed and did in this
session: the useful result, any failed attempt, and unfinished work. Preserve
earlier observations on the same row. State uncertainty plainly; a tool error
or an unperformed check is not a successful result. The terminal runner fills
the known structured facts on this row after the fire ends.
{{/if}}
