from setuptools import setup, find_packages
from setuptools.command.install import install
import os
import sys
import shutil
import site
from pathlib import Path


def _find_comfyui_custom_nodes() -> Path | None:
    """Walk common locations to find ComfyUI/custom_nodes/."""
    candidates = [
        Path("/root/ComfyUI/custom_nodes"),
        Path.home() / "ComfyUI" / "custom_nodes",
        Path("/opt/ComfyUI/custom_nodes"),
        Path("/workspace/ComfyUI/custom_nodes"),
    ]

    for drive in ["C", "D", "E"]:
        candidates.append(Path(f"{drive}:/ComfyUI/custom_nodes"))
        candidates.append(Path(f"{drive}:/AI/ComfyUI/custom_nodes"))

    cwd = Path.cwd()
    for parent in [cwd] + list(cwd.parents):
        cn = parent / "custom_nodes"
        if cn.exists() and (parent / "main.py").exists():
            return cn
        cn2 = parent / "ComfyUI" / "custom_nodes"
        if cn2.exists():
            return cn2

    for c in candidates:
        if c.exists():
            return c
    return None


class PostInstall(install):
    def run(self):
        install.run(self)
        self._deploy_to_custom_nodes()

    def _deploy_to_custom_nodes(self):
        target_dir = _find_comfyui_custom_nodes()
        if target_dir is None:
            return

        dest = target_dir / "ComfyUI-Enhanced"
        if dest.exists():
            return

        pkg_dir = None
        for sp in site.getsitepackages() + [site.getusersitepackages()]:
            candidate = Path(sp) / "comfyui_enhanced"
            if candidate.exists():
                pkg_dir = candidate
                break

        if pkg_dir is None:
            src = Path(__file__).resolve().parent
            if (src / "__init__.py").exists() and (src / "core").exists():
                pkg_dir = src

        if pkg_dir:
            shutil.copytree(str(pkg_dir), str(dest), dirs_exist_ok=True)


setup(
    name="comfyui-enhanced",
    version="1.0.0",
    description="Image enhancement nodes for ComfyUI",
    packages=find_packages(),
    package_data={"": ["web/*.html", "requirements.txt"]},
    include_package_data=True,
    install_requires=[
        "websockets>=12.0",
        "psutil>=5.9.0",
        "requests>=2.31.0",
    ],
    cmdclass={"install": PostInstall},
    python_requires=">=3.10",
)
