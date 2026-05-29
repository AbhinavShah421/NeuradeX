FROM maven:3.9.6-eclipse-temurin-21 AS build
WORKDIR /app
COPY pom.xml .
RUN mvn dependency:go-offline -q
COPY src ./src
RUN mvn package -DskipTests -q

FROM amazoncorretto:21-alpine
WORKDIR /app
COPY --from=build /app/target/trade-executor-1.0.0.jar app.jar
EXPOSE 8011
ENTRYPOINT ["java", "-jar", "app.jar"]
