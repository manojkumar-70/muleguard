import java.io.BufferedWriter;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.Locale;
import java.util.Map;
import java.util.Random;
import java.util.TreeMap;

public final class SyntheticTransactionGenerator {
    private static final long SEED = 20261002L;
    private static final int NORMAL_TRANSACTION_COUNT = 1_000;
    private static final int MULE_ACCOUNT_COUNT = 20;
    private static final int INBOUND_PER_MULE = 8;
    private static final int OUTBOUND_PER_MULE = 2;
    private static final String CURRENCY = "INR";
    private static final Instant START_TIME = Instant.parse("2025-01-01T00:00:00Z");
    private static final String[] DOCUMENTATION_IPS = {
            "192.0.2.10", "198.51.100.20", "203.0.113.30", "192.0.2.40"
    };
    private static final String[] HEADERS = {
            "transaction_id", "sender", "receiver", "amount_paise", "currency",
            "timestamp", "device_id", "ip_address", "status", "scenario_label"
    };

    private SyntheticTransactionGenerator() {
    }

    public static void main(String[] args) throws IOException {
        Path output = args.length == 0
                ? Path.of("java-engine", "data", "transactions.csv")
                : Path.of(args[0]);
        Path parent = output.toAbsolutePath().getParent();
        if (parent != null) {
            Files.createDirectories(parent);
        }

        Random random = new Random(SEED);
        Map<String, String> evaluationRoles = new TreeMap<>();
        try (BufferedWriter writer = Files.newBufferedWriter(
                output, StandardCharsets.UTF_8)) {
            writeRow(writer, HEADERS);
            int transactionId = 1;
            transactionId = writeNormalTransactions(
                    writer, random, transactionId, evaluationRoles);
            writeSuspiciousNetwork(writer, random, transactionId, evaluationRoles);
        }
        writeEvaluationManifest(
                manifestPath(output, parent), evaluationRoles);

        System.out.printf(
                Locale.ROOT,
                "Generated %d transactions (%d NORMAL, %d SYNTHETIC_SUSPICIOUS) at %s%n",
                NORMAL_TRANSACTION_COUNT + MULE_ACCOUNT_COUNT
                        * (INBOUND_PER_MULE + OUTBOUND_PER_MULE),
                NORMAL_TRANSACTION_COUNT,
                MULE_ACCOUNT_COUNT * (INBOUND_PER_MULE + OUTBOUND_PER_MULE),
                output);
    }

    private static int writeNormalTransactions(
            BufferedWriter writer,
            Random random,
            int transactionId,
            Map<String, String> evaluationRoles) throws IOException {
        for (int i = 0; i < NORMAL_TRANSACTION_COUNT; i++) {
            String sender = accountId("N", random.nextInt(500) + 1);
            String receiver;
            do {
                receiver = accountId("N", random.nextInt(500) + 1);
            } while (sender.equals(receiver));

            writeTransaction(
                    writer,
                    transactionId++,
                    sender,
                    receiver,
                    random.nextLong(10_000L, 5_000_001L),
                    random,
                    "NORMAL");
            evaluationRoles.put(sender, "NORMAL");
            evaluationRoles.put(receiver, "NORMAL");
        }
        return transactionId;
    }

    private static void writeSuspiciousNetwork(
            BufferedWriter writer,
            Random random,
            int transactionId,
            Map<String, String> evaluationRoles) throws IOException {
        for (int mule = 1; mule <= MULE_ACCOUNT_COUNT; mule++) {
            String muleAccount = accountId("M", mule);
            evaluationRoles.put(muleAccount, "FOCAL_SUSPICIOUS");
            String deviceId = String.format(Locale.ROOT, "DEV-S-%03d", (mule - 1) % 4 + 1);
            String ipAddress = DOCUMENTATION_IPS[(mule - 1) % DOCUMENTATION_IPS.length];

            for (int source = 1; source <= INBOUND_PER_MULE; source++) {
                String sourceAccount = accountId(
                        "S", (mule - 1) * INBOUND_PER_MULE + source);
                writeTransaction(
                        writer,
                        transactionId++,
                        sourceAccount,
                        muleAccount,
                        random.nextLong(50_000L, 500_001L),
                        random,
                        deviceId,
                        ipAddress,
                        "SYNTHETIC_SUSPICIOUS");
                evaluationRoles.put(sourceAccount, "SOURCE_PARTICIPANT");
            }

            for (int destination = 1; destination <= OUTBOUND_PER_MULE; destination++) {
                String destinationAccount = accountId(
                        "D", (mule - 1) * OUTBOUND_PER_MULE + destination);
                writeTransaction(
                        writer,
                        transactionId++,
                        muleAccount,
                        destinationAccount,
                        random.nextLong(100_000L, 900_001L),
                        random,
                        deviceId,
                        ipAddress,
                        "SYNTHETIC_SUSPICIOUS");
                evaluationRoles.put(destinationAccount, "DESTINATION_PARTICIPANT");
            }
        }
    }

    private static Path manifestPath(Path transactionOutput, Path parent) {
        if ("transactions.csv".equals(transactionOutput.getFileName().toString())) {
            return parent.resolve("evaluation_account_roles.csv");
        }
        String filename = transactionOutput.getFileName().toString();
        int extension = filename.lastIndexOf('.');
        String stem = extension > 0 ? filename.substring(0, extension) : filename;
        return parent.resolve(stem + "_evaluation_roles.csv");
    }

    private static void writeEvaluationManifest(
            Path output, Map<String, String> evaluationRoles) throws IOException {
        try (BufferedWriter writer = Files.newBufferedWriter(
                output, StandardCharsets.UTF_8)) {
            writeRow(writer, new String[] {"account_id", "evaluation_role"});
            for (Map.Entry<String, String> entry : evaluationRoles.entrySet()) {
                writeRow(writer, new String[] {entry.getKey(), entry.getValue()});
            }
        }
    }

    private static void writeTransaction(
            BufferedWriter writer,
            int transactionId,
            String sender,
            String receiver,
            long amountPaise,
            Random random,
            String scenarioLabel) throws IOException {
        String deviceId = String.format(Locale.ROOT, "DEV-N-%03d", random.nextInt(100) + 1);
        String ipAddress = DOCUMENTATION_IPS[random.nextInt(DOCUMENTATION_IPS.length)];
        writeTransaction(
                writer, transactionId, sender, receiver, amountPaise, random,
                deviceId, ipAddress, scenarioLabel);
    }

    private static void writeTransaction(
            BufferedWriter writer,
            int transactionId,
            String sender,
            String receiver,
            long amountPaise,
            Random random,
            String deviceId,
            String ipAddress,
            String scenarioLabel) throws IOException {
        String timestamp = START_TIME
                .plusSeconds(random.nextLong(0, 365L * 24 * 60 * 60))
                .truncatedTo(ChronoUnit.SECONDS)
                .toString();
        String status = random.nextInt(100) < 97 ? "SUCCESS" : "DECLINED";
        writeRow(writer, new String[] {
                String.format(Locale.ROOT, "TXN-%06d", transactionId),
                sender,
                receiver,
                Long.toString(amountPaise),
                CURRENCY,
                timestamp,
                deviceId,
                ipAddress,
                status,
                scenarioLabel
        });
    }

    private static String accountId(String type, int number) {
        return String.format(Locale.ROOT, "ACC-%s-%06d", type, number);
    }

    private static void writeRow(BufferedWriter writer, String[] columns) throws IOException {
        for (int i = 0; i < columns.length; i++) {
            if (i > 0) {
                writer.write(',');
            }
            writer.write(columns[i]);
        }
        writer.newLine();
    }
}
