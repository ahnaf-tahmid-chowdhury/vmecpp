"""The usual hodgepodge of helper utilities without a better home."""

import contextlib
import importlib.metadata
import logging
import os
import shutil
import subprocess
import tempfile
from collections.abc import Generator
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def package_root() -> Path:
    """Return the absolute path of Python package root, i.e.
    PATH/TO/VMECPP/REPO/src/vmecpp.

    Useful e.g. to point tests to test files using paths relative to the repo root
    rather than paths relative to the test module.
    """
    return Path(__file__).parent


def distribution_root() -> Path:
    """The path to the install location of this package. This may be the same as
    package_root, but doesn't have to be.

    The two differ in editable installations, where package_root() will point to the
    source files, and distribution will point the /site-packages/vmecpp folder of your
    python environment. It is the correct path to use for accessing shared libraries and
    executables that come with vmecpp.
    """
    return importlib.metadata.distribution("vmecpp").locate_file("vmecpp")


@contextlib.contextmanager
def change_working_directory_to(path: Path) -> Generator[None, None, None]:
    """Changes the working director within a context manager.

    Args:
        path: The path to change the working directory to.
    """
    origin = Path.cwd()

    try:
        os.chdir(path)
        yield
    finally:
        os.chdir(origin)


def get_vmec_configuration_name(vmec_file: Path) -> str:
    """Given a VMEC input file (case_name.json or input.case_name) or output file
    (wout_case_name.nc) extract the 'case_name' section of the name and return it."""
    filename = vmec_file.name

    if filename.endswith(".json"):
        case_name = filename[:-5]
    elif filename.startswith("input."):
        case_name = filename[6:]
    elif filename.startswith("wout_") and filename.endswith(".nc"):
        case_name = filename[5:-3]
    else:
        msg = f"This does not look like a VMEC input or output file: {filename}"
        raise ValueError(msg)

    return case_name


def indata_to_json(
    filename: Path,
    use_mgrid_file_absolute_path: bool = False,
    output_override: Path | None = None,
) -> Path:
    """Convert a VMEC2000 INDATA file to a VMEC++ JSON input file.

    The new file is created in the current working directory. Given
    `input.name`, the corresponding JSON file will be called
    `name.json` if `output_override` is None, otherwise that Path
    is used as output path.

    Args:
        filename: The path to the VMEC2000 INDATA file.
        use_mgrid_file_absolute_path: If True, the absolute path to
            the parent directory of `filename` will be prepended to
            the output mgrid_file path.
        output_override: If present, indata_to_json writes the output
            to this Path. Otherwise for an input file called "input.XYZ"
            the output file will be placed in the same directory as
            the input and will have name "XYZ.json".

    Returns:
        The absolute path to the newly created JSON file.
    """
    if not filename.exists():
        msg = f"{filename} does not exist."
        raise FileNotFoundError(msg)

    indata_to_json_exe = (
        distribution_root() / "cpp" / "third_party" / "indata2json" / "indata2json"
    )
    if not indata_to_json_exe.is_file():
        msg = f"{indata_to_json_exe} is not a file."
        raise FileNotFoundError(msg)
    if not os.access(indata_to_json_exe, os.X_OK):
        msg = f"Missing permission to execute {indata_to_json_exe}."
        raise PermissionError(msg)

    original_input_file = filename.absolute()
    original_cwd = Path.cwd()

    if output_override is not None:
        # in case output_override is a relative path, we must resolve it
        # before we cd into the temporary working directory otherwise we
        # won't know where to copy the final output to anymore.
        output_override = output_override.resolve()

    with (
        tempfile.TemporaryDirectory() as tmpdir,
        change_working_directory_to(Path(tmpdir)),
    ):
        # The Fortran indata2json supports a limited length of the path to the input file.
        # We work in a temporary directory in which we copy the input so that paths are always short.
        local_input_file = original_input_file.name
        shutil.copyfile(original_input_file, local_input_file)

        if use_mgrid_file_absolute_path:
            command = [
                indata_to_json_exe,
                "--mgrid_folder",
                original_input_file.parent.absolute(),
                local_input_file,
            ]
        else:
            command = [indata_to_json_exe, local_input_file]
        result = subprocess.run(command, check=True)

        if result.returncode != 0:
            raise subprocess.CalledProcessError(
                result.returncode, [indata_to_json_exe, local_input_file]
            )

        configuration_name = get_vmec_configuration_name(filename)
        i2j_output_file = Path(f"{configuration_name}.json")

        if not i2j_output_file.is_file():
            msg = (
                "The indata2json command was executed with no errors but output file "
                f"{i2j_output_file} is missing. This should never happen!"
            )
            raise RuntimeError(msg)

        if output_override is None:
            # copy output back
            path_to_vmecpp_input_file = Path(original_cwd, i2j_output_file)
            shutil.copyfile(i2j_output_file, path_to_vmecpp_input_file)
            return path_to_vmecpp_input_file

        # otherwise copy output to desired output override
        shutil.copyfile(i2j_output_file, output_override)
        return output_override


# adapted from https://github.com/jonathanschilling/indata2json/blob/4274976/json2indata
def vmecpp_json_to_indata(vmecpp_json: dict[str, Any]) -> str:
    """Convert a dictionary with the contents of a VMEC++ JSON input file to the
    corresponding conents of a VMEC2000 INDATA file."""

    indata: str = "&INDATA\n"

    indata += "\n  ! numerical resolution, symmetry assumption\n"
    indata += _bool_to_namelist("lasym", vmecpp_json)
    for varname in ("nfp", "mpol", "ntor", "ntheta", "nzeta"):
        indata += _int_to_namelist(varname, vmecpp_json)

    indata += "\n  ! multi-grid steps\n"
    indata += _int_array_to_namelist("ns_array", vmecpp_json)
    indata += _float_array_to_namelist("ftol_array", vmecpp_json)
    indata += _int_array_to_namelist("niter_array", vmecpp_json)

    indata += "\n  ! solution method tweaking parameters\n"
    indata += _float_to_namelist("delt", vmecpp_json)
    indata += _float_to_namelist("tcon0", vmecpp_json)
    indata += _float_array_to_namelist("aphi", vmecpp_json)
    indata += _bool_to_namelist("lforbal", vmecpp_json)

    indata += "\n  ! printout interval\n"
    indata += _int_to_namelist("nstep", vmecpp_json)

    indata += "\n  ! total enclosed toroidal magnetic flux\n"
    indata += _float_to_namelist("phiedge", vmecpp_json)

    indata += "\n  ! mass / pressure profile\n"
    indata += _string_to_namelist("pmass_type", vmecpp_json)
    indata += _float_array_to_namelist("am", vmecpp_json)
    indata += _float_array_to_namelist("am_aux_s", vmecpp_json)
    indata += _float_array_to_namelist("am_aux_f", vmecpp_json)
    indata += _float_to_namelist("pres_scale", vmecpp_json)
    indata += _float_to_namelist("gamma", vmecpp_json)
    indata += _float_to_namelist("spres_ped", vmecpp_json)

    indata += "\n  ! select constraint on iota or enclosed toroidal current profiles\n"
    indata += _int_to_namelist("ncurr", vmecpp_json)

    indata += "\n  ! (initial guess for) iota profile\n"
    indata += _string_to_namelist("piota_type", vmecpp_json)
    indata += _float_array_to_namelist("ai", vmecpp_json)
    indata += _float_array_to_namelist("ai_aux_s", vmecpp_json)
    indata += _float_array_to_namelist("ai_aux_f", vmecpp_json)

    indata += "\n  ! enclosed toroidal current profile\n"
    indata += _string_to_namelist("pcurr_type", vmecpp_json)
    indata += _float_array_to_namelist("ac", vmecpp_json)
    indata += _float_array_to_namelist("ac_aux_s", vmecpp_json)
    indata += _float_array_to_namelist("ac_aux_f", vmecpp_json)
    indata += _float_to_namelist("curtor", vmecpp_json)
    indata += _float_to_namelist("bloat", vmecpp_json)

    indata += "\n  ! free-boundary parameters\n"
    indata += _bool_to_namelist("lfreeb", vmecpp_json)
    indata += _string_to_namelist("mgrid_file", vmecpp_json)
    indata += _float_array_to_namelist("extcur", vmecpp_json)
    indata += _int_to_namelist("nvacskip", vmecpp_json)

    indata += "\n  ! initial guess for magnetic axis\n"
    indata += _float_array_to_namelist("raxis_cc", vmecpp_json)
    indata += _float_array_to_namelist("zaxis_cs", vmecpp_json)
    indata += _float_array_to_namelist("raxis_cs", vmecpp_json)
    indata += _float_array_to_namelist("zaxis_cc", vmecpp_json)

    indata += "\n  ! (initial guess for) boundary shape\n"
    indata += _fourier_coefficients_to_namelist("rbc", vmecpp_json)
    indata += _fourier_coefficients_to_namelist("zbs", vmecpp_json)
    indata += _fourier_coefficients_to_namelist("rbs", vmecpp_json)
    indata += _fourier_coefficients_to_namelist("zbc", vmecpp_json)

    indata += "\n/\n"

    return indata


def _bool_to_namelist(varname: str, vmecpp_json: dict[str, Any]) -> str:
    if varname not in vmecpp_json:
        return ""

    return f"  {varname} = {'.true.' if vmecpp_json[varname] else '.false.'}\n"


def _string_to_namelist(varname: str, vmecpp_json: dict[str, Any]) -> str:
    if varname not in vmecpp_json:
        return ""

    return f"  {varname} = '{vmecpp_json[varname]}'\n"


def _int_to_namelist(varname: str, vmecpp_json: dict[str, Any]) -> str:
    if varname not in vmecpp_json:
        return ""

    return f"  {varname} = {vmecpp_json[varname]}\n"


def _float_to_namelist(varname: str, vmecpp_json: dict[str, Any]) -> str:
    if varname not in vmecpp_json:
        return ""

    return f"  {varname} = {vmecpp_json[varname]:.20e}\n"


def _int_array_to_namelist(varname: str, vmecpp_json: dict[str, Any]) -> str:
    if varname in vmecpp_json and len(vmecpp_json[varname]) > 0:
        elements = ", ".join(map(str, vmecpp_json[varname]))
        return f"  {varname} = {elements}\n"
    return ""


def _float_array_to_namelist(varname: str, vmecpp_json: dict[str, Any]) -> str:
    if varname in vmecpp_json and len(vmecpp_json[varname]) > 0:
        elements = ", ".join([f"{x:.20e}" for x in vmecpp_json[varname]])
        return f"  {varname} = {elements}\n"
    return ""


def _fourier_coefficients_to_namelist(varname: str, vmecpp_json: dict[str, Any]) -> str:
    if varname in vmecpp_json and len(vmecpp_json[varname]) > 0:
        out = ""
        for coefficient in vmecpp_json[varname]:
            m = coefficient["m"]
            n = coefficient["n"]
            value = coefficient["value"]
            out += f"  {varname}({n}, {m}) = {value:.20e}\n"
        return out
    return ""
