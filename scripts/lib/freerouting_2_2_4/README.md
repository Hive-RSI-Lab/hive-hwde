# Freerouting 2.2.4, one class patched

`app/freerouting/board/BasicBoard.java` is Freerouting's file at tag v2.2.4
(commit 20f1a72) with a bound on the outer loop of `normalize_traces`, and
`BasicBoard.class` is that file compiled. `routelib.build_fr_cmd` puts this
folder ahead of `freerouting-2.2.4.jar` on the classpath, so the jar itself
stays the upstream one. Freerouting is GPL-3.0 (https://github.com/freerouting/freerouting);
these two files are under its licence, not the module's MIT (NOTICE).

**The defect.** A routed trace that lies along a fixed wire of its own net and
ends at a corner or interior point of it can't split the fixed wire (deletion is
forbidden), so `PolylineTrace.split` splits the routed trace, `normalize`
recombines it, and the top-level call reports a change on every pass. The loop
never ends and the router logs nothing. `route_escape`'s stubs (#477) and locked
tracks with a near-axis jog (#657, #665) both trigger it; which boards do depends
on where the routes happen to land, so a check of the DSN can't predict it.
`tests/fixtures/freerouting/stub_overlap.dsn` is a one-net repro.

**The change.** After 50 passes the loop stops, logs a line starting
`hwde: normalize_traces stopped`, and leaves the traces as they are. Runs that
never reach 50 passes don't change.

**Remove it** when the tools move to a Freerouting with its own cap: 2.5.0 has
`MAX_NORMALIZE_ITERATIONS`, and `build_fr_cmd` only overlays the 2.2.4 jar.

**Rebuild** (a JDK 25, from this folder; only `BasicBoard.class` is kept):

    javac -implicit:none -nowarn -proc:none -sourcepath /nonexistent \
      -cp freerouting-2.2.4.jar -d . app/freerouting/board/BasicBoard.java
