cmake_minimum_required(VERSION 3.18)
project(vmecpp C CXX)

set(CMAKE_CXX_STANDARD 20)

if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

set(CMAKE_CXX_FLAGS "-fPIC -Wall -Wextra")
set(CMAKE_CXX_FLAGS_DEBUG "-O0 -g")
set(CMAKE_CXX_FLAGS_RELEASE "-O3 -fno-math-errno")

# use ccache if available
find_program(CCACHE_COMMAND NAMES ccache ccache-swig)
if(EXISTS ${CCACHE_COMMAND})
  message(STATUS "Found ccache: ${CCACHE_COMMAND}")
  set(CMAKE_CXX_COMPILER_LAUNCHER ${CCACHE_COMMAND})
else()
  message(STATUS "Could NOT find ccache")
endif()

find_package(HDF5 REQUIRED COMPONENTS C CXX)
include_directories(${HDF5_INCLUDE_DIRS} ${HDF5_CXX_INCLUDE_DIRS})

find_package (netCDF)
if (NOT netCDF_FOUND)
  # Finds the netCDF installation using CMake's PkgConfig
  set(netCDF_PC_FILE netcdf)
  find_package(PkgConfig REQUIRED)
  set(PKG_CONFIG_USE_CMAKE_PREFIX_PATH TRUE)
  pkg_check_modules(netCDF REQUIRED ${netCDF_PC_FILE}>=4.6.0 IMPORTED_TARGET)
  pkg_get_variable(netCDF_PREFIX ${netCDF_PC_FILE} prefix)
  message(STATUS "netCDF prefix: ${netCDF_PREFIX}")
  message(STATUS "netCDF include dirs: ${netCDF_INCLUDE_DIRS}")
  message(STATUS "netCDF libraries: ${netCDF_LIBRARIES}")
endif()

include_directories(${netCDF_INCLUDE_DIRS})

find_package(Eigen3 REQUIRED)
include_directories(${EIGEN3_INCLUDE_DIR})

find_package(nlohmann_json REQUIRED)

find_package(LAPACK REQUIRED)

find_package(OpenMP)

include_directories(${PROJECT_SOURCE_DIR}/abscab-cpp)
set (abscab_sources "abscab-cpp/abscab/abscab.cc" "abscab-cpp/abscab/abscab.hh")

include_directories(${PROJECT_SOURCE_DIR}/abseil-cpp)
add_subdirectory(${PROJECT_SOURCE_DIR}/abseil-cpp)

add_subdirectory(${PROJECT_SOURCE_DIR}/indata2json)

# Allow to retain include paths as used for Bazel build.
# This needs to be defined before add_subdirectory(src) is called,
# which starts including files that want to pull in header files
# specified relative to `${PROJECT_SOURCE_DIR}/src/vmecpp/cpp`.
include_directories(${PROJECT_SOURCE_DIR}/src/vmecpp/cpp)

# Assemble the VMEC++ source tree.
# Start out with ABSCAB sources - no need for a separate library for ABSCAB.
set (vmecpp_sources ${abscab_sources})
add_subdirectory(src)

# Define a static library for the actual computation core of VMEC++.
add_library (vmecpp_core STATIC ${vmecpp_sources})
target_link_libraries(vmecpp_core PRIVATE ${HDF5_CXX_LIBRARIES} ${HDF5_LIBRARIES})
target_link_libraries(vmecpp_core PRIVATE ${netCDF_LIBRARIES})
target_link_libraries(vmecpp_core PRIVATE nlohmann_json::nlohmann_json)
target_link_libraries(vmecpp_core PRIVATE LAPACK::LAPACK)
target_link_libraries(vmecpp_core PRIVATE absl::algorithm absl::base absl::synchronization absl::strings absl::str_format absl::log absl::string_view absl::check absl::status absl::statusor)
if(OpenMP_CXX_FOUND)
  target_link_libraries(vmecpp_core PRIVATE OpenMP::OpenMP_CXX)
endif()

# Now also add the vmec_standalone executable.
add_executable (vmec_standalone ${PROJECT_SOURCE_DIR}/src/vmecpp/cpp/vmecpp/vmec/vmec_standalone/vmec_standalone.cc)
target_link_libraries (vmec_standalone vmecpp_core)
target_link_libraries(vmecpp_core PRIVATE absl::strings)

# Now add the pybind11 module for VMEC++.
add_subdirectory(pybind11)
set (vmecpp_pybind11_sources
  ${PROJECT_SOURCE_DIR}/src/vmecpp/cpp/vmecpp/vmec/pybind11/pybind_vmec.cc
  ${PROJECT_SOURCE_DIR}/src/vmecpp/cpp/vmecpp/vmec/pybind11/vmec_indata_pywrapper.cc
  ${PROJECT_SOURCE_DIR}/src/vmecpp/cpp/vmecpp/vmec/pybind11/vmec_indata_pywrapper.h
)
pybind11_add_module(_vmecpp ${vmecpp_pybind11_sources})
target_link_libraries(_vmecpp PRIVATE vmecpp_core)

install(TARGETS _vmecpp LIBRARY DESTINATION vmecpp/cpp/.)
install(TARGETS indata2json DESTINATION vmecpp/cpp/third_party/indata2json/)
