"""NIM Panel — NIM_calculation dashboard'unun izole PRISMA portu.

Kaynak: doguctan/NIM_calculation @ bs_evolution5 (c569ae3). Kapsam ve faz
plani icin bkz. docs/DASHBOARD_ADAPTATION_PLAN.md.

Izolasyon sozlesmesi:
- Bu modul `presentations`, `prisma_home` veya diger moduellerden HICBIR SEY
  import etmez; onlara hicbir sey enjekte etmez.
- Uygulamaya tek dokunusu app.py'deki korumali blueprint kaydidir; kayit
  basarisiz olursa uygulamanin geri kalani etkilenmez.
- Veri kaynagi kaynak reponun kendi SQL'leridir (PRISMA_DEP_* KULLANILMAZ).
"""
from .routes import nim_panel_bp

__all__ = ["nim_panel_bp"]
