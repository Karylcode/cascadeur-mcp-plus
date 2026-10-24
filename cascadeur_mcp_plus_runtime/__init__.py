"""What Cascadeur executes from an installed wheel.

An installed wheel puts this directory (cascadeur_mcp_plus_runtime/) in site-packages with

    start_mcp_plus.py
    cascadeur_mcp_plus/          the in-app server package

and the proxy/CLI pass that one directory to Cascadeur, which adds only it to sys.path. The
host Python's site-packages (compiled packages built for another Python version) therefore
never shadows Cascadeur's own modules. A source checkout keeps both next to start_mcp_plus.py
at the repository root instead; cascadeur_mcp_plus/start.py holds the shared start logic.
"""
