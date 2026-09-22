"""The local web application.

A stdlib HTTP server on 127.0.0.1 and a frontend of hand-written ES modules.
No web framework, no bundler, no `node_modules`, no new runtime dependency.

The reasoning is in `docs/architecture/milestone-3-local-product.md`: this
repository declares nine runtime dependencies and a comment explaining why each
one earned its place, and adding FastAPI, uvicorn, Vite and React to serve a
single page to a single person on a single machine would be the largest
dependency decision in the project's history.

The boundary that matters: **the frontend never touches SQLite.** It speaks
HTTP/JSON to this server, which is the only thing holding a connection. That is
where authentication would go if this ever stopped being an N=1 alpha, and it
is why the seam exists now, when it is free.
"""
