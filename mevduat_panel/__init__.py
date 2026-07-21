"""Mevduat Panel — NIM_calculation dashboard'unun (deposit tarafı) izole PRISMA portu.

Kaynak: doguctan/NIM_calculation @ bs_evolution5 (c569ae3). Kapsam ve faz
plani icin bkz. docs/DASHBOARD_ADAPTATION_PLAN.md.

Izolasyon sozlesmesi:
- Bu modul `presentations`, `prisma_home` veya diger moduellerden HICBIR SEY
  import etmez; onlara hicbir sey enjekte etmez.
- Uygulamaya tek dokunusu app.py'deki korumali blueprint kaydidir; kayit
  basarisiz olursa uygulamanin geri kalani etkilenmez.
- Veri kaynagi kaynak reponun kendi SQL'leridir (PRISMA_DEP_* KULLANILMAZ).
"""
from .routes import mevduat_panel_bp

# Route modülleri blueprint'i import edip endpoint ekler (presentations
# modülündeki çok-dosyalı route deseni). Sıra: routes önce (blueprint tanımı).
from . import routes_cost  # noqa: E402,F401
from . import routes_np  # noqa: E402,F401
from . import routes_outstanding  # noqa: E402,F401
from . import routes_sector  # noqa: E402,F401
from . import routes_weekly  # noqa: E402,F401

__all__ = ["mevduat_panel_bp"]
