package kr.hwpkit;

import java.io.File;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.PrintStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.AtomicMoveNotSupportedException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.Arrays;
import java.util.Properties;

import kr.dogfoot.hwp2hwpx.Hwp2Hwpx;
import kr.dogfoot.hwplib.object.HWPFile;
import kr.dogfoot.hwplib.object.fileheader.FileHeader;
import kr.dogfoot.hwplib.reader.ForFileHeader;
import kr.dogfoot.hwplib.reader.HWPReader;
import kr.dogfoot.hwplib.util.compoundFile.reader.CompoundFileReader;
import kr.dogfoot.hwplib.util.compoundFile.reader.StreamReader;
import kr.dogfoot.hwpxlib.object.HWPXFile;
import kr.dogfoot.hwpxlib.writer.HWPXWriter;

/**
 * Headless HWP 5.x -> HWPX converter.
 *
 * Usage: java -jar hwp2hwpx-all.jar IN.hwp OUT.hwpx
 *        java -jar hwp2hwpx-all.jar --version
 *
 * Exit codes: 0 ok, 10 encrypted, 11 corrupt, 12 unsupported format, 1 other.
 * On failure, exactly one line "hwpkit-java: CODE: message" goes to stderr.
 */
public final class Convert {
    static final int OK = 0;
    static final int FAILED = 1;
    static final int ENCRYPTED = 10;
    static final int CORRUPT = 11;
    static final int UNSUPPORTED = 12;

    private static final byte[] OLE_MAGIC = {
            (byte) 0xD0, (byte) 0xCF, 0x11, (byte) 0xE0, (byte) 0xA1, (byte) 0xB1, 0x1A, (byte) 0xE1};
    private static final byte[] ZIP_MAGIC = {0x50, 0x4B, 0x03, 0x04};
    private static final byte[] HWP3_PREFIX = "HWP Document File V3".getBytes(StandardCharsets.US_ASCII);

    static final class Failure extends Exception {
        final int code;

        Failure(int code, String message) {
            super(message);
            this.code = code;
        }
    }

    public static void main(String[] args) {
        if (args.length == 1 && args[0].equals("--version")) {
            System.out.println(buildInfo().getProperty("upstream.sha", "unknown"));
            System.exit(OK);
        }
        if (args.length != 2) {
            fail(FAILED, "usage: java -jar hwp2hwpx-all.jar <in.hwp> <out.hwpx> | --version");
        }
        // hwplib/hwpxlib may print stack traces or diagnostics; keep our stdio contract clean.
        PrintStream realOut = System.out;
        PrintStream realErr = System.err;
        PrintStream sink = new PrintStream(OutputStream.nullOutputStream());
        int code = OK;
        String message = null;
        try {
            System.setOut(sink);
            System.setErr(sink);
            convert(new File(args[0]), new File(args[1]));
        } catch (Failure f) {
            code = f.code;
            message = f.getMessage();
        } catch (Throwable t) {
            code = FAILED;
            message = describe(t);
        } finally {
            System.setOut(realOut);
            System.setErr(realErr);
        }
        if (code != OK) {
            fail(code, message);
        }
        System.exit(OK);
    }

    static void convert(File in, File out) throws Failure {
        if (!in.isFile()) {
            throw new Failure(FAILED, "input not found or not a regular file: " + in);
        }
        sniff(in);
        checkHeader(in);

        HWPFile hwp;
        try {
            hwp = HWPReader.fromFile(in);
        } catch (Throwable t) {
            String msg = String.valueOf(t.getMessage()).toLowerCase();
            if (msg.contains("password")) {
                throw new Failure(ENCRYPTED, "password-protected document");
            }
            throw new Failure(CORRUPT, "cannot parse HWP body: " + describe(t));
        }

        HWPXFile hwpx;
        try {
            hwpx = Hwp2Hwpx.toHWPX(hwp);
        } catch (Throwable t) {
            throw new Failure(FAILED, "hwp2hwpx conversion failed: " + describe(t));
        }

        Path dest = out.toPath().toAbsolutePath();
        Path tmp = dest.resolveSibling(dest.getFileName() + ".tmp-" + ProcessHandle.current().pid());
        try {
            HWPXWriter.toFilepath(hwpx, tmp.toString());
            try {
                Files.move(tmp, dest, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING);
            } catch (AtomicMoveNotSupportedException e) {
                Files.move(tmp, dest, StandardCopyOption.REPLACE_EXISTING);
            }
        } catch (Throwable t) {
            throw new Failure(FAILED, "cannot write " + dest + ": " + describe(t));
        } finally {
            try {
                Files.deleteIfExists(tmp);
            } catch (IOException ignored) {
            }
        }
    }

    /** Cheap signature sniff so non-HWP inputs never reach hwplib. */
    private static void sniff(File in) throws Failure {
        byte[] head = new byte[32];
        int n;
        try (InputStream is = Files.newInputStream(in.toPath())) {
            n = is.readNBytes(head, 0, head.length);
        } catch (IOException e) {
            throw new Failure(FAILED, "cannot read input: " + describe(e));
        }
        if (startsWith(head, n, OLE_MAGIC)) {
            return;
        }
        if (startsWith(head, n, HWP3_PREFIX)) {
            throw new Failure(UNSUPPORTED, "HWP 3.x document (only HWP 5.x is supported)");
        }
        if (startsWith(head, n, ZIP_MAGIC)) {
            throw new Failure(UNSUPPORTED, "ZIP container (already HWPX/OWPML?), not HWP 5.x");
        }
        if (n < OLE_MAGIC.length) {
            throw new Failure(CORRUPT, "file too short to be HWP (" + n + " bytes)");
        }
        throw new Failure(CORRUPT, "not an HWP file (no OLE compound signature)");
    }

    private static boolean startsWith(byte[] buf, int n, byte[] prefix) {
        return n >= prefix.length && Arrays.equals(buf, 0, prefix.length, prefix, 0, prefix.length);
    }

    /** Reads only the FileHeader stream: format/version/encryption checks before the full parse. */
    private static void checkHeader(File in) throws Failure {
        CompoundFileReader cfr;
        try {
            cfr = new CompoundFileReader(in);
        } catch (Throwable t) {
            throw new Failure(CORRUPT, "damaged OLE compound file: " + describe(t));
        }
        try {
            if (!cfr.listChildNames().contains("FileHeader")) {
                throw new Failure(UNSUPPORTED, "OLE compound file without FileHeader stream (not HWP 5.x)");
            }
            FileHeader fh = new FileHeader();
            try {
                StreamReader sr = cfr.getChildStreamReader("FileHeader", false, null);
                try {
                    ForFileHeader.read(fh, sr);
                } finally {
                    sr.close();
                }
            } catch (Throwable t) {
                if (String.valueOf(t.getMessage()).contains("not hwp file")) {
                    throw new Failure(UNSUPPORTED, "FileHeader signature is not HWP 5.x");
                }
                throw new Failure(CORRUPT, "unreadable FileHeader: " + describe(t));
            }
            int major = fh.getVersion().getMM();
            if (major != 5) {
                throw new Failure(UNSUPPORTED, "HWP format version " + major + ".x (only 5.x is supported)");
            }
            if (fh.hasPassword()) {
                throw new Failure(ENCRYPTED, "password-protected document");
            }
            if (fh.isDRMDocument() || fh.isPublicCertificationDRMDocument() || fh.isEncryptPublicCertification()) {
                throw new Failure(ENCRYPTED, "DRM/certificate-encrypted document");
            }
        } finally {
            try {
                cfr.close();
            } catch (Throwable ignored) {
            }
        }
    }

    private static Properties buildInfo() {
        Properties p = new Properties();
        try (InputStream is = Convert.class.getResourceAsStream("build-info.properties")) {
            if (is != null) {
                p.load(is);
            }
        } catch (IOException ignored) {
        }
        return p;
    }

    private static String describe(Throwable t) {
        String m = t.getMessage();
        String s = t.getClass().getSimpleName() + (m == null ? "" : ": " + m);
        return s.replaceAll("[\\r\\n]+", " ");
    }

    private static void fail(int code, String message) {
        System.err.println("hwpkit-java: " + code + ": " + message.replaceAll("[\\r\\n]+", " "));
        System.exit(code);
    }
}
