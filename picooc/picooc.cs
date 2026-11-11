// PICOOC Latin-S reader for Windows.
// Protocol per openScale's PicoocHandler (GPL-3.0): service FFF0, notify FFF1, write FFF2.
// usage: picooc.exe <hexaddr> <male|female> <heightCm> <age> [waitSeconds] [csvFile]
using System;
using System.IO;
using System.Threading;
using Windows.Devices.Bluetooth;
using Windows.Devices.Bluetooth.Advertisement;
using Windows.Devices.Bluetooth.GenericAttributeProfile;
using Windows.Storage.Streams;

class Picooc {
    const byte PREFIX = 0xF1;
    static object lk = new object();
    static StreamWriter log;
    static GattCharacteristic tx;
    static bool male; static int heightCm, age;
    static double weight, impedance; static string composition = "";
    static ManualResetEvent finished = new ManualResetEvent(false);
    static System.Collections.Generic.List<string> history = new System.Collections.Generic.List<string>();

    static void W(string s) { lock (lk) { string l = DateTime.Now.ToString("HH:mm:ss.fff") + " " + s; Console.WriteLine(l); log.WriteLine(l); } }
    static string Hex(byte[] b) { return BitConverter.ToString(b).Replace("-", " "); }
    static int U16(byte[] d, int i) { return (d[i] << 8) | d[i + 1]; }
    static T G<T>(Windows.Foundation.IAsyncOperation<T> op) {
        var i = (Windows.Foundation.IAsyncInfo)op;
        while (i.Status == Windows.Foundation.AsyncStatus.Started) Thread.Sleep(5);
        if (i.Status != Windows.Foundation.AsyncStatus.Completed) throw new Exception("async " + i.Status + " " + i.ErrorCode);
        return op.GetResults();
    }

    static void Send(byte[] data, string what) {
        lock (tx) {
            var w = new DataWriter(); w.WriteBytes(data);
            var st = G(tx.WriteValueAsync(w.DetachBuffer(), GattWriteOption.WriteWithResponse));
            W("TX " + Hex(data) + " (" + what + ") " + st);
        }
    }
    static byte[] Ack(byte op) { return new byte[] { PREFIX, 0x03, op }; }
    static bool gotFresh;

    // Body composition from weight + impedance, using the published formulas openScale's
    // StandardImpedanceLib uses (Sun 2003 FFM/TBW, Janssen SMM, Katch-McArdle BMR).
    // Not PICOOC's own algorithm: good for trends, absolute values will differ from the app.
    const string CsvHeader = "time,weight_kg,impedance_ohm,bmi,fat_pct,water_pct,muscle_pct,bone_kg,bmr_kcal,source";
    static string Comp(double w, double imp) {
        double hm = heightCm / 100.0, bmi = w / (hm * hm);
        if (imp < 100 || imp > 1500) return string.Format("{0:0.0},,,,,", bmi);
        double h2r = heightCm * (double)heightCm / imp;
        double ffm = male ? -10.68 + 0.65 * h2r + 0.26 * w + 0.02 * imp : -9.53 + 0.69 * h2r + 0.17 * w + 0.02 * imp;
        double fat = (1 - ffm / w) * 100;
        double tbw = 0.99513 * (male ? 1.2 + 0.45 * h2r + 0.18 * w : 3.75 + 0.45 * h2r + 0.11 * w);
        double smm = 0.401 * h2r + 3.825 * (male ? 1 : 0) - 0.071 * age + 5.102;
        double bone = (male ? 0.057 : 0.05) * ffm, bmr = ffm * 21.6 + 370;
        return string.Format("{0:0.0},{1:0.0},{2:0.0},{3:0.0},{4:0.00},{5:0}", bmi, fat, tbw / w * 100, smm / w * 100, bone, bmr);
    }
    static string Row(DateTime when, double w, double imp, string source) {
        return string.Format("{0:yyyy-MM-dd HH:mm:ss},{1:0.00},{2:0.0},{3},\"{4}\"", when, w, imp, Comp(w, imp), source);
    }
    static string Summary(double w, double imp) {
        var c = Comp(w, imp).Split(',');
        if (c[1] == "") return string.Format("RESULT weight {0:0.00} kg  BMI {1}  (no impedance - stand barefoot for body fat)", w, c[0]);
        return string.Format("RESULT weight {0:0.00} kg  BMI {1}  fat {2}%  water {3}%  skeletal muscle {4}%  bone {5} kg  BMR {6} kcal  (impedance {7:0.0} ohm)",
            w, c[0], c[1], c[2], c[3], c[4], c[5], imp);
    }

    static void OnFrame(byte[] d) {
        if (d.Length == 0) return;
        byte op = d[0];
        W("RX " + Hex(d));
        switch (op) {
            case 0x35: case 0x3A: {
                uint t = (uint)DateTimeOffset.UtcNow.ToUnixTimeSeconds();
                Send(new byte[] { PREFIX, 0x0A, op, (byte)(t >> 24), (byte)(t >> 16), (byte)(t >> 8), (byte)t, 0x00, 0x00 /*kg*/, 0x00 }, "time sync");
                break; }
            case 0x30:
                Send(Ack(0x30), "ack 0x30");
                Thread.Sleep(100);
                int h = (int)Math.Round((heightCm * 10 - 1000) / 5.0); h = Math.Max(0, Math.Min(255, h));
                Send(new byte[] { 0x31, 0x06, 0x01, (byte)(male ? 1 : 2), (byte)h, (byte)Math.Max(18, Math.Min(255, age)) }, "user profile");
                break;
            case 0x39:
                Send(Ack(0x39), "ack 0x39");
                if (d.Length >= 10) {
                    weight = U16(d, 6) / 20.0; double imp = U16(d, 8) / 10.0; if (imp > 0) impedance = imp;
                    bool done = d.Length < 16 || d[15] == 0;
                    W(string.Format(">>> weight {0:0.00} kg  impedance {1:0.0} ohm  {2}", weight, imp, done ? "STABLE" : "measuring"));
                }
                break;
            case 0x32:
                Send(Ack(0x32), "ack 0x32");
                if (d.Length >= 20) {
                    composition = string.Format("fat={0:0.0}% water={1:0.0}% bone={2:0.00}kg visceral={3} bodyAge={4} impedance={5}",
                        U16(d, 4) / 10.0, U16(d, 6) / 10.0, U16(d, 10) / 20.0, d[14], d[15], U16(d, 18));
                    W(">>> composition (weight " + (U16(d, 2) / 20.0).ToString("0.00") + "kg) " + composition);
                }
                break;
            case 0x36:
                // Offline record: [36][len][epoch BE 4B][weight/20][impedance/10]
                Send(Ack(0x36), "ack 0x36");
                if (d.Length >= 10) {
                    var when = DateTimeOffset.FromUnixTimeSeconds(((long)d[2] << 24) | ((long)d[3] << 16) | ((long)d[4] << 8) | d[5]).LocalDateTime;
                    double hw = U16(d, 6) / 20.0, hi = U16(d, 8) / 10.0;
                    W(string.Format(">>> record {0:yyyy-MM-dd HH:mm:ss} weight {1:0.00} kg impedance {2:0.0} ohm", when, hw, hi));
                    lock (history) history.Add(Row(when, hw, hi, "scale record"));
                    // The scale measures on its own and hands the result over as a stored record
                    // once the link is up, so a record from the last few minutes is the reading.
                    if ((DateTime.Now - when).TotalMinutes < 10) { gotFresh = true; W(Summary(hw, hi)); }
                }
                break;
            case 0x37: case 0x3C: case 0x3E: case 0x3F:
                Send(Ack(op), "ack 0x" + op.ToString("X2"));
                break;
        }
    }

    static void Main(string[] a) {
        ulong addr = Convert.ToUInt64(a[0], 16);
        male = a[1].ToLower().StartsWith("m"); heightCm = int.Parse(a[2]); age = int.Parse(a[3]);
        int wait = a.Length > 4 ? int.Parse(a[4]) : 120;
        string csv = a.Length > 5 ? a[5] : "weights.csv";
        log = new StreamWriter(Path.ChangeExtension(csv, ".log"), true) { AutoFlush = true };
        // Keep reconnecting until a live weight arrives: the scale often wakes (and hangs up after
        // ~20 s) before anyone is standing on it.
        var deadline = DateTime.Now.AddSeconds(wait);
        while (weight <= 0 && !gotFresh) {
            int left = (int)(deadline - DateTime.Now).TotalSeconds;
            if (left <= 0) { W("gave up: no live weight within " + wait + "s"); break; }
            finished.Reset();
            try { Run(addr, left, csv); } catch (Exception e) { W("ERROR " + e.Message); Thread.Sleep(1000); }
        }
        log.Close();
    }

    static void Run(ulong addr, int wait, string csv) {
        W("waiting for scale " + addr.ToString("X12") + " - step on it now (" + wait + "s)");
        var seen = new ManualResetEvent(false);
        var aw = new BluetoothLEAdvertisementWatcher { ScanningMode = BluetoothLEScanningMode.Active };
        aw.Received += (s, e) => { if (e.BluetoothAddress == addr) seen.Set(); };
        aw.Start(); bool ok = seen.WaitOne(wait * 1000); aw.Stop();
        if (!ok) { W("scale not seen"); return; }

        var dev = G(BluetoothLEDevice.FromBluetoothAddressAsync(addr));
        dev.ConnectionStatusChanged += (s, e) => { W("link " + s.ConnectionStatus); if (s.ConnectionStatus == BluetoothConnectionStatus.Disconnected) finished.Set(); };
        var svc = G(dev.GetGattServicesForUuidAsync(BluetoothUuidHelper.FromShortId(0xFFF0), BluetoothCacheMode.Uncached)).Services[0];
        var chars = G(svc.GetCharacteristicsAsync(BluetoothCacheMode.Uncached)).Characteristics;
        GattCharacteristic rx = null;
        foreach (var c in chars) { uint id = BluetoothUuidHelper.TryGetShortId(c.Uuid) ?? 0; if (id == 0xFFF1) rx = c; if (id == 0xFFF2) tx = c; }
        rx.ValueChanged += (s, e) => {
            var b = new byte[e.CharacteristicValue.Length]; DataReader.FromBuffer(e.CharacteristicValue).ReadBytes(b);
            ThreadPool.QueueUserWorkItem(_ => { try { OnFrame(b); } catch (Exception ex) { W("handler error " + ex.Message); } });
        };
        var p = rx.CharacteristicProperties;
        var cccd = (p & GattCharacteristicProperties.Indicate) != 0 ? GattClientCharacteristicConfigurationDescriptorValue.Indicate : GattClientCharacteristicConfigurationDescriptorValue.Notify;
        W("connected, subscribe FFF1: " + G(rx.WriteClientCharacteristicConfigurationDescriptorAsync(cccd)));

        finished.WaitOne(90 * 1000);
        Thread.Sleep(300);
        dev.Dispose();
        bool newFile = !File.Exists(csv);
        using (var f = new StreamWriter(csv, true)) {
            if (newFile) f.WriteLine(CsvHeader);
            lock (history) { foreach (var h in history) f.WriteLine(h); history.Clear(); }
            if (weight > 0) f.WriteLine(Row(DateTime.Now, weight, impedance, "live " + composition));
        }
        if (weight > 0) { W(Summary(weight, impedance)); if (composition != "") W("scale's own figures: " + composition); }
        else if (!gotFresh) W("session ended without a new reading, waiting for the next wake-up");
    }
}

