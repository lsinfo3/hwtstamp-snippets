import fileinput
from datetime import datetime
from argparse import ArgumentParser

parser = ArgumentParser()
parser.add_argument("-t", "--type",
                    help="type to be written into the csv file",
                    default="hardware")
parser.add_argument("-f", "--file",
                    help="write all measured delays to a csv file; 'TYPE' is replaced by the given type; 'TIME' is replaced by a time stamp",
                    default=f"/tmp/ts_TYPE_TIME.csv")
parser.add_argument("-o", "--offset",
                    help="offset to add to the delay values before print/display (calibration)",
                    type=int, default="0")
args = parser.parse_args()

tap1_map = {}
tap2_map = {}

try:
    csvfile = None
    if args.file != None:
        x = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        parsed_file = args.file.replace("TYPE", args.type).replace("TIME", x)
        csvfile = open(parsed_file, "w", buffering=1)
        csvfile.write("rep;type;delay;time1;time2\n")
        print(f"Output file: {parsed_file}")

    rep = -1
    for line in fileinput.input(("-",)):
        tap = line.split(" ")[0]
        time = int(line.split(" ")[1].split(".")[1])
        rest = " ".join(line.split(" ")[2:])

        if tap == "tap1":
            if rest in tap2_map:
                time2 = tap2_map[rest]
                del tap2_map[rest]
                print(f"delay: {time2 - time + args.offset} ns,   packet: {' '.join(line.rstrip().split(' ')[1:])[:72]}")

                if csvfile != None:
                    rep += 1
                    csvfile.write(f"{rep};{args.type};{time2 - time + args.offset};{time};{time2}\n")
            else:
                tap1_map[rest] = time
                
        elif tap == "tap2":
            if rest in tap1_map:
                time1 = tap1_map[rest]
                del tap1_map[rest]
                print(f"delay: {time - time1 + args.offset} ns,   packet: {' '.join(line.rstrip().split(' ')[1:])[:72]}")

                if csvfile != None:
                    rep += 1
                    csvfile.write(f"{rep};{args.type};{time - time1 + args.offset};{time1};{time}\n")
            else:
                tap2_map[rest] = time

except Exception as e:
    if args.file != None:
        csvfile.close()
    if line:
        print("---")
        print(f"ERROR {line=}")
    raise e
