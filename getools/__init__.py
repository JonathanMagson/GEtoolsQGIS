def classFactory(iface):
    from .plugin import GEtoolsPlugin

    return GEtoolsPlugin(iface)
