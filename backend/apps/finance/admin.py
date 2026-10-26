# Finance models are tenant-scoped (schema-per-organization) and are managed
# from the in-app dashboard, not Django admin — the admin site always runs
# against the shared `public` schema, so registering these here would only
# ever show the empty template tables, never a real organization's data.
