#!/bin/bash

if ! [ -x "$(command -v docker)" ]; then
  echo 'Error: Please install docker first.' >&2
  exit 1
fi

if ! [ -d global-workflow ]; then
  ./scripts/checkout_gfs.sh
fi

INTEL_COMP_DIR=/data/intel
COMP=${COMP:-intel}
REPO=${REPO:-dshawul}

#loads intel compiler from host and builds image with it
build_image() {
    docker build --build-arg REPO=${REPO} -t ${1} -f ${2} .
    if docker inspect temp &> /dev/null; then
       echo $'\tremoving existing temp image'
       docker rm -f temp 
    fi
    docker run --name temp -v ${INTEL_COMP_DIR}:${INTEL_COMP_DIR} -d -it ${1} bash
    docker exec -it temp /bin/bash -c "${3}"
    docker commit temp ${1}
    docker rm -f temp 
}

#netcdf
IMAGE_NAME=${REPO}/netcdf-intel
DOCKER_FILE=Dockerfiles/intel/Dockerfile-netcdf
COMMAND="cd /opt; source intel_comp.sh; COMP=${COMP} ./build_netcdf.sh"
build_image ${IMAGE_NAME} ${DOCKER_FILE} "${COMMAND}"

#esmf
IMAGE_NAME=${REPO}/esmf-intel
DOCKER_FILE=Dockerfiles/intel/Dockerfile-esmf
COMMAND="cd /opt; source intel_comp.sh; COMP=${COMP} ./build_esmf.sh"
build_image ${IMAGE_NAME} ${DOCKER_FILE} "${COMMAND}"

#fv3
IMAGE_NAME=${REPO}/fv3-intel
DOCKER_FILE=Dockerfiles/intel/Dockerfile-fv3
COMMAND="cd /opt; source intel_comp.sh; COMP=${COMP} ./build_fv3.sh; ./copy_deps.sh"
build_image ${IMAGE_NAME} ${DOCKER_FILE} "${COMMAND}"

#nceplibs
IMAGE_NAME=${REPO}/nceplibs-intel
DOCKER_FILE=Dockerfiles/intel/Dockerfile-nceplibs
COMMAND="cd /opt; ./checkout_nceplibs.sh; source intel_comp.sh; COMP=${COMP} ./build_nceplibs.sh"
build_image ${IMAGE_NAME} ${DOCKER_FILE} "${COMMAND}"

#gfs
IMAGE_NAME=${REPO}/gfs-intel
DOCKER_FILE=Dockerfiles/intel/Dockerfile-gfs
COMMAND="cd /opt; source intel_comp.sh; ./patch_gfs.sh; cd global-workflow/sorc; ./build_all.sh; ./link_fv3gfs.sh emc linux.intel"
build_image ${IMAGE_NAME} ${DOCKER_FILE} "${COMMAND}"

