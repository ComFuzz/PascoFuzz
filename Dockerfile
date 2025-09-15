FROM ubuntu:22.04
LABEL "about"="PascoFuzz docker image"

ARG DEBIAN_FRONTEND=noninteractive

# Install dependencies
RUN apt-get update && apt-get upgrade -y && \
    apt-get install -y --no-install-suggests --no-install-recommends \
    gnupg curl ca-certificates systemctl \
    python3-pip python3-setuptools python3-wheel \
    ninja-build build-essential flex bison \
    git cmake iproute2 \
    libsctp-dev libgnutls28-dev libgcrypt-dev libssl-dev \
    libidn11-dev libmongoc-dev libbson-dev libyaml-dev \
    libnghttp2-dev libmicrohttpd-dev libcurl4-gnutls-dev libnghttp2-dev \
    libtins-dev libtalloc-dev libsctp-dev lksctp-tools \
    meson \
    tcpdump

# Install dependencies
COPY requirements.txt /pascofuzz/requirements.txt
RUN pip3 install -r /pascofuzz/requirements.txt
RUN apt-get install -y libcapture-tiny-perl libdatetime-perl libdevel-cover-perl \
    libdigest-md5-file-perl libfile-spec-perl libjson-xs-perl \
    libmodule-load-conditional-perl libscalar-list-utils-perl libtime-hires-perl 
RUN cpan App::cpanminus && cpanm Memory::Process

# Install MongoDB
RUN curl -fsSL https://pgp.mongodb.com/server-6.0.asc | \
    gpg -o /usr/share/keyrings/mongodb-server-6.0.gpg --dearmor && \
    echo "\
deb [arch=amd64,arm64 signed-by=/usr/share/keyrings/mongodb-server-6.0.gpg] \
https://repo.mongodb.org/apt/ubuntu jammy/mongodb-org/6.0 multiverse" | \
    tee /etc/apt/sources.list.d/mongodb-org-6.0.list && \
    apt-get update && apt-get install -y mongodb-org && \
    systemctl start mongod && systemctl enable mongod

# Install Open5GS v2.7.5
WORKDIR /pascofuzz
RUN git clone https://github.com/open5gs/open5gs /pascofuzz/open5gs
WORKDIR /pascofuzz/open5gs
RUN git checkout v2.7.5 && rm -rf install && mkdir install
RUN meson build --prefix=/ -Db_coverage=true && \
    ninja -C build
RUN cd build && ninja install && cd .. && cp build/tests/app/5gc /usr/bin/
COPY sample-v2.7.5.yaml /pascofuzz/open5gs/build/configs/sample.yaml

# Install mongocxx/bsoncxx
WORKDIR /pascofuzz
RUN curl -OL https://github.com/mongodb/mongo-cxx-driver/releases/download/r3.7.0/mongo-cxx-driver-r3.7.0.tar.gz && \
    tar -xzf mongo-cxx-driver-r3.7.0.tar.gz
RUN cd mongo-cxx-driver-r3.7.0/build && \
    cmake .. -DCMAKE_BUILD_TYPE=Release -DCMAKE_CXX_STANDARD=17 && \
    cmake --build . -j "$(nproc)" && \
    cmake --build . --target install
RUN cp -r /pascofuzz/mongo-cxx-driver-r3.7.0/build/install/include/bsoncxx/v_noabi/bsoncxx/ /usr/include/ && \
    cp -r /pascofuzz/mongo-cxx-driver-r3.7.0/build/install/include/mongocxx/v_noabi/mongocxx/ /usr/include/ && \
    cp /pascofuzz/mongo-cxx-driver-r3.7.0/build/install/lib/libbsoncxx.so /usr/lib/ && \
    cp /pascofuzz/mongo-cxx-driver-r3.7.0/build/install/lib/libmongocxx.so /usr/lib/

# COPY modified UERANSIM (with FieldPools) nr-* binaries
WORKDIR /pascofuzz
COPY UERANSIM_PascoFuzz/build/nr-* /usr/bin/

# Install lcov
RUN git clone https://github.com/linux-test-project/lcov.git \
    /pascofuzz/lcov
WORKDIR /pascofuzz/lcov
RUN git checkout v2.2 && make install

WORKDIR /pascofuzz
ENTRYPOINT systemctl start mongod && /bin/bash
