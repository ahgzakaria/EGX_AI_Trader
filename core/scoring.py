# ==========================================================
# هذا الملف بقى مجرد "إعادة توجيه" (Backward-Compatible Shim)
# ==========================================================
#
# كل منطق القرار الفعلي انتقل بالكامل لـ:
#
#       strategy/decision_engine.py
#
# وده الملف الوحيد فى المشروع كله المسموح له يقرر
# BUY / WATCH / AVOID (زي ما اتفقنا فى Sprint 1).
#
# الملف ده باقي بس عشان أي كود قديم بينادي:
#
#       from core.scoring import score_stock
#
# (زي backtesting/engine.py و core/scanner.py) يفضل شغال
# من غير أي تعديل فيه.
# ==========================================================

from strategy.decision_engine import evaluate as score_stock


__all__ = ["score_stock"]