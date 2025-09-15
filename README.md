## Build and Run Docker
```shell
docker image build -t pascofuzz:open5gs .
docker run --rm -v $(pwd):/pascofuzz --name pascofuzz_open5gs --privileged -it pascofuzz:open5gs bash
```
## Start PascoFuzz in Parallel
```shell
./scripts/init_db.py /pascofuzz/open5gs/
./run_parallel.py
```

