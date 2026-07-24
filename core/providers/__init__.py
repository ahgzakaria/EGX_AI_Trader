"""Isolated external-provider adapters for the hybrid daily bridge.

These adapters are validation/research components. They are never wired into a
trading strategy, the normalized cache, or production routing during audit. They
read credentials from the process environment and never expose them.
"""
