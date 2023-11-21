import pandas as pd
import os
import argparse
from typing import List, Tuple


def rawETevents_to_pandas(event_file: str) -> pd.DataFrame:
    """
    Convert bidsphysio events file to a Pandas DataFrame.

    Parameters:
        event_file (str): The path to the raw event file.

    Returns:
        pd.DataFrame: A DataFrame containing event information.
    """
    onset_list: List[float] = []
    duration_list: List[float] = []
    trial_type_list: List[str] = []

    with open(event_file, "r") as file:
        lines = file.readlines()

    start_line: Tuple[float, str] = None

    for line in lines:
        line = line.strip()

        if "start" in line:
            parts = line.split("\t")
            onset = float(parts[0])
            trial_type = parts[2].split(" ")[1]
            start_line = (onset, trial_type)

        elif "stop" in line or "end" in line:
            parts = line.split("\t")
            trial_type_stop = parts[2].split(" ")[1]
            stop_onset = float(parts[0])

            if trial_type_stop == start_line[1]:
                duration = stop_onset - start_line[0]
                onset_list.append(start_line[0])
                duration_list.append(duration)
                trial_type_list.append(start_line[1])
                start_line = None
        elif "hello" in line:
            parts = line.split("\t")
            onset = float(parts[0])
            onset_list.append(onset)
            duration_list.append(0)
            trial_type_list.append("start task")
        elif "bye" in line:
            parts = line.split("\t")
            onset = float(parts[0])
            onset_list.append(onset)
            duration_list.append(0)
            trial_type_list.append("stop task")
        elif "RECORD" in line:
            parts = line.split("\t")
            onset = float(parts[0])
            onset_list.append(onset)
            duration_list.append(0)
            trial_type_list.append("ET start")

    data = {
        "onset": onset_list,
        "duration": duration_list,
        "trial_type": trial_type_list,
    }
    df = pd.DataFrame(data)
    return df


def rawETevents_to_BIDS(event_file: str) -> None:
    """
    Convert bidsphysio events file to a BIDS-compatible stim file.

    Parameters:
        event_file (str): The path to the bidsphysio event file.
    """
    stim_file_name = event_file.replace("eventlist_raw", "recording-eyetrack_stim")
    eventdf = rawETevents_to_pandas(event_file)
    eventdf.to_csv(stim_file_name, sep="\t", index=False)


def main() -> None:
    """
    Main function for converting bidsphysio eye-tracking event files to BIDS format.
    """
    parser = argparse.ArgumentParser(
        description="Convert raw event files from bidsphysio to BIDS-compatible STIM files."
    )

    parser.add_argument(
        "-i", "--input", required=True, help="Path to the BIDS compatible folder"
    )

    args = parser.parse_args()
    folder_path = args.input
    subfolders = ["func", "dwi"]

    for subfolder in subfolders:
        subfolder_path = os.path.join(folder_path, subfolder)

        if os.path.exists(subfolder_path):
            for root, dirs, files in os.walk(subfolder_path):
                for file in files:
                    if file.endswith("eventlist_raw.tsv"):
                        event_file = os.path.join(root, file)
                        rawETevents_to_BIDS(event_file)
                        print(f"Converted {event_file} to BIDS format.")


if __name__ == "__main__":
    main()
