ARG SPARK_VERSION=4.1.1
ARG SPARK_JAVA_VERSION=21
ARG ICEBERG_VERSION=1.10.1
ARG ICEBERG_SPARK_RUNTIME=4.0_2.13

FROM alpine:3.22 AS iceberg-runtime
ARG ICEBERG_VERSION
ARG ICEBERG_SPARK_RUNTIME
RUN wget -q -O /iceberg-spark-runtime.jar \
    "https://repo1.maven.org/maven2/org/apache/iceberg/iceberg-spark-runtime-${ICEBERG_SPARK_RUNTIME}/${ICEBERG_VERSION}/iceberg-spark-runtime-${ICEBERG_SPARK_RUNTIME}-${ICEBERG_VERSION}.jar"

FROM docker.io/library/spark:${SPARK_VERSION}-java${SPARK_JAVA_VERSION}-python3
ARG ICEBERG_VERSION
ARG ICEBERG_SPARK_RUNTIME

LABEL org.opencontainers.image.source="https://github.com/kzh/infra"
LABEL org.opencontainers.image.description="Apache Spark with Apache Iceberg runtime"

USER root
COPY --from=iceberg-runtime /iceberg-spark-runtime.jar /opt/spark/jars/iceberg-spark-runtime-${ICEBERG_SPARK_RUNTIME}-${ICEBERG_VERSION}.jar
RUN chmod 0644 /opt/spark/jars/iceberg-spark-runtime-${ICEBERG_SPARK_RUNTIME}-${ICEBERG_VERSION}.jar
USER 185
