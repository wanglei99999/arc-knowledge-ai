FROM elasticsearch:8.13.4

# 从本地文件安装 IK 分词插件，避免构建时网络依赖
# 构建前需先将 elasticsearch-analysis-ik-8.13.4.zip 下载到此目录
COPY elasticsearch-analysis-ik-8.13.4.zip /tmp/analysis-ik.zip
RUN ./bin/elasticsearch-plugin install --batch file:///tmp/analysis-ik.zip \
    && rm /tmp/analysis-ik.zip
