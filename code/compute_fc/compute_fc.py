""" Python script to denoise and aggregate timeseries and, using the latter, compute functional
connectivity matrices from BIDS derivatives (e.g. fmriprep).

Run as (see 'python compute_fc.py -h' for options):

    python compute_fc.py path_to_BIDS_derivatives

In the context of HCPh (pilot), it would be:

    python compute_fc.py /data/datasets/hcph-pilot/derivatives/fmriprep-23.1.4/
"""

import argparse
import logging
import os
import os.path as op

import matplotlib.pyplot as plt
import numpy as np
from bids import BIDSLayout
from bids.layout import parse_file_entities
from bids.layout.writing import build_path
from matplotlib.cm import get_cmap
from matplotlib.lines import Line2D
from nilearn.connectome import ConnectivityMeasure, vec_to_sym_matrix
from nilearn.datasets import fetch_atlas_difumo
from nilearn.interfaces.fmriprep import load_confounds
from nilearn.interfaces.fmriprep.load_confounds import _load_single_confounds_file
from nilearn.maskers import MultiNiftiMapsMasker
from nilearn.plotting import plot_design_matrix, plot_matrix
from nilearn.signal import clean, _handle_scrubbed_volumes, _sanitize_confounds
from nilearn._utils import stringify_path
from sklearn.covariance import GraphicalLassoCV, LedoitWolf

from nilearn_patcher import MultiNiftiMapsMasker as MultiNiftiMapsMasker_patched

FC_PATTERN = [
    "sub-{subject}[/ses-{session}]/func/sub-{subject}"
    "[_ses-{session}][_task-{task}][_meas-{meas}]"
    "_{suffix}{extension}"
]
FC_FILLS = {"suffix": "relmat", "extension": ".tsv"}
TIMESERIES_PATTERN = [
    "sub-{subject}[/ses-{session}]/func/sub-{subject}"
    "[_ses-{session}][_task-{task}][_desc-{desc}]"
    "_{suffix}{extension}"
]
TIMESERIES_FILLS = {"desc": "denoised", "extension": ".tsv"}
FIGURE_PATTERN = [
    "sub-{subject}/figures/sub-{subject}[_ses-{session}]"
    "[_task-{task}][_meas-{meas}][_desc-{desc}]"
    "_{suffix}{extension}",
    "sub-{subject}/figures/sub-{subject}[_ses-{session}]"
    "[_task-{task}][_desc-{desc}]_{suffix}{extension}",
]
FIGURE_FILLS = {"extension": "png"}
CONFOUND_PATTERN = [
    "sub-{subject}[_ses-{session}][_task-{task}][_part-{part}][_desc-{desc}]"
    "_{suffix}{extension}"
]
CONFOUND_FILLS = {"desc": "confounds", "suffix": "timeseries", "extension": "tsv"}

DENOISING_STRATEGY = ["high_pass", "motion", "scrub"]

TS_FIGURE_SIZE = (50, 25)
FC_FIGURE_SIZE = (50, 45)
LABELSIZE = 22
NETWORK_MAPPING = "yeo_networks7"  # Also yeo_networks17
NETWORK_CMAP = "turbo"


def get_arguments():
    parser = argparse.ArgumentParser(
        description="""Compute functional connectivity matrices from fmriprep
                    output directory.""",
    )

    # Input/Output arguments and options
    parser.add_argument("data_dir", help="BIDS dataset or derivatives with data")
    parser.add_argument(
        "-s", "--save", action="store_true", default=False, help="save the outputs"
    )
    parser.add_argument(
        "-o", "--output", default=None, help="specify an alternative output directory"
    )

    # Script specific options
    parser.add_argument(
        "--overwrite", default=False, action="store_true", help="force computation"
    )
    parser.add_argument(
        "--task",
        default=[],
        action="store",
        nargs="+",
        help="a space delimited list of task(s)",
    )

    # fMRI and denoising specific options
    # parser.add_argument(
    #     "--denoise-only", default=False, action="store_true", help="NOT IMPLEMENTED"
    # )
    parser.add_argument(
        "--atlas-dimension",
        default=64,
        type=int,
        help="dimension of the atlas (usually 64, 128 or 512)",
    )
    parser.add_argument(
        "--low-pass",
        default=0.15,
        action="store",
        type=float,
        help="cutoff frequency of low pass filtering",
    )
    parser.add_argument(
        "--FD-thresh",
        default=0.4,
        action="store",
        type=float,
        help="framewise displacement threshold (in mm)",
    )
    parser.add_argument(
        "--SDVARS-thresh",
        default=3,
        action="store",
        type=float,
        help="standardised DVAR threshold",
    )
    parser.add_argument(
        "--n-scrub-frames",
        default=5,
        action="store",
        type=int,
        help="minimum segment length after volume censoring",
    )
    parser.add_argument(
        "--fc-estimator",
        default="sparse inverse covariance",
        action="store",
        type=str,
        help="""type of connectivity to compute (can be 'correlation', 'covariance' or
        'sparse')""",
    )
    parser.add_argument(
        "--no-censor",
        default=False,
        action="store_true",
        help="interpolate volumes with high motion without censoring",
    )

    parser.add_argument(
        "-v",
        "--verbosity",
        action="count",
        default=1,
        help="""increase output verbosity (-v: standard logging infos; -vv: logging
        infos and NiLearn verbose; -vvv: debug)""",
    )

    args = parser.parse_args()

    return args


def get_func_filenames_bids(paths_to_func_dir, task_filter=[]):
    logging.debug("Using BIDS to find functional files...")

    layout = BIDSLayout(
        paths_to_func_dir,
        validate=False,
    )

    all_derivatives = layout.get(
        scope="all",
        return_type="file",
        extension="nii.gz",
        session=[15],
        suffix="bold",
        task=task_filter,
    )

    t_rs = []
    for file in all_derivatives:
        t_rs.append(layout.get_metadata(file)["RepetitionTime"])

    unique_tr_s = set(t_rs)

    if len(unique_tr_s) > 1:
        logging.warning(
            "Multiple TR values found, temporal filtering may not" " work as intended !"
        )

    return all_derivatives, list(unique_tr_s)[0]


def get_bids_savename(filename, patterns=FC_PATTERN, **kwargs):
    entity = parse_file_entities(filename)

    for key, value in kwargs.items():
        entity[key] = value

    bids_savename = build_path(entity, patterns)
    logging.debug(f"BIDS filename is :\n\t{build_path(entity, patterns)}")

    return bids_savename


def get_atlas_data(atlas_name="DiFuMo", **kwargs):
    logging.info("Fetching the DiFuMo atlas ...")

    if kwargs["dimension"] not in [64, 128, 512]:
        logging.warning("Dimension for DiFuMo atlas is different from 64, 128 or 512 ! Are you certain you want to deviate from those optimized modes? ")

    return fetch_atlas_difumo(legacy_format=False, **kwargs)


def find_derivative(path, derivatives_name="derivatives"):
    if op.split(path)[-1] == derivatives_name:
        return path
    if op.split(path)[0] == "":
        logging.error(f'"{derivatives_name}" could not be found on path !')
        return ""
    return find_derivative(path=op.split(path)[0])


def check_existing_output(output, func_filename, return_existing=False, **kwargs):
    logging.debug("\n\t".join(func_filename))

    missing_data_filter = [
        not op.exists(op.join(output, get_bids_savename(filename, **kwargs)))
        for filename in func_filename
    ]

    missing_data = np.array(func_filename)[missing_data_filter]
    logging.debug(f"\t{sum(missing_data_filter)} missing data found")
    logging.debug("\n\t".join(missing_data))

    if return_existing:
        existing_data = np.array(func_filename)[
            [not fltr for fltr in missing_data_filter]
        ]
        return missing_data.tolist(), existing_data.tolist()
    return missing_data.tolist()


def load_timeseries(func_filename, output):
    if len(func_filename):
        logging.info(f"Loading existing timeseries for {len(func_filename)} files ...")

    loaded_ts = []
    for filename in func_filename:
        path_to_ts = get_bids_savename(
            filename, patterns=TIMESERIES_PATTERN, **TIMESERIES_FILLS
        )
        logging.debug(f"\t{op.join(output, path_to_ts)}")
        loaded_ts.append(
            np.genfromtxt(op.join(output, path_to_ts), float, delimiter="\t")
        )

    return loaded_ts


def get_confounds_manually(func_filename, **kwargs):
    confounds, sample_mask = [], []

    for filename in func_filename:
        dir_name = op.dirname(filename)
        confounds_file = op.join(
            dir_name,
            get_bids_savename(filename, patterns=CONFOUND_PATTERN, **CONFOUND_FILLS),
        )

        # confounds_json_file = load_confounds._get_json(confounds_file)
        confounds_json_file = confounds_file.replace("tsv", "json")
        individual_sm, individual_conf = _load_single_confounds_file(
            confounds_file=confounds_file,
            confounds_json_file=confounds_json_file,
            **kwargs,
        )
        confounds.append(individual_conf)
        sample_mask.append(individual_sm)

    return confounds, sample_mask


def fit_transform_patched(
    func_filename, atlas_filename, confounds=None, sample_mask=None, **kwargs
):
    masker = MultiNiftiMapsMasker(maps_img=atlas_filename, **kwargs)

    try:
        time_series = masker.fit_transform(
            func_filename, confounds=confounds, sample_mask=sample_mask
        )
    except ValueError:
        # See nilearn issue #3967 for more details
        logging.warning("Using patched version of 'MultiNiftiMapsMasker ...'")
        masker = MultiNiftiMapsMasker_patched(maps_img=atlas_filename, **kwargs)

        time_series = masker.fit_transform(
            func_filename, confounds=confounds, sample_mask=sample_mask
        )

    return time_series


def interpolate_and_denoise_timeseries(
    func_filename,
    atlas_filename,
    confounds,
    sample_mask,
    t_r=None,
    low_pass=None,
    output=None,
    verbose=2,
):
    logging.info("Interpolating signal (no censoring) ...")
    # Extract the regional signals
    extracted_time_series = fit_transform_patched(
        func_filename,
        atlas_filename,
        standardize="zscore_sample",
        verbose=verbose,
        n_jobs=8,
    )

    interpolated_signals = []
    interpolated_confounds = []
    denoised_signals = []
    for ts, conf, sm, fn in zip(
        extracted_time_series, confounds, sample_mask, func_filename
    ):
        logging.debug(
            f"Timeserie has length {ts.shape[0]} and sample mask has "
            f"length {len(sm)}"
        )

        conf = _sanitize_confounds(ts.shape[0], n_runs=1, confounds=conf)
        conf = stringify_path(conf)

        ts_to_interpolate = ts.copy()

        inter_sig, inter_conf = _handle_scrubbed_volumes(
            signals=ts_to_interpolate,
            confounds=conf,
            sample_mask=sm,
            filter_type="butterworth",
            t_r=t_r,
        )

        if output is not None:
            plot_interpolation(ts, inter_sig, fn, output)

        # Denoise the signals
        denoised_sig = clean(
            inter_sig,
            standardize="zscore_sample",
            confounds=inter_conf,
            low_pass=low_pass,
            t_r=t_r,
        )

        interpolated_signals.append(inter_sig)
        interpolated_confounds.append(inter_conf)
        denoised_signals.append(denoised_sig)

    return denoised_signals, interpolated_confounds


def plot_interpolation(ts, interpolated_ts, filename, output):
    ax = plot_timeseries_signal(ts)
    ax = plot_timeseries_signal(interpolated_ts, color="tab:red", ax=ax, linewidth=2)

    legend_elements = [
        Line2D([0], [0], color=col, label=lab)
        for lab, col in zip(["Timeserie", "Interpolation"], ["tab:blue", "tab:red"])
    ]

    ax.legend(
        handles=legend_elements,
        ncol=2,
        loc="upper left",
        bbox_to_anchor=(0, 1.04),
        fontsize=LABELSIZE,
    )

    interpolate_saveloc = get_bids_savename(
        filename,
        patterns=FIGURE_PATTERN,
        desc="interpolatedtimeseries",
        **FIGURE_FILLS,
    )

    logging.debug("Saving interpolated timeseries visual report at:")
    logging.debug(f"\t{op.join(output, interpolate_saveloc)}")
    os.makedirs(op.join(output, op.dirname(interpolate_saveloc)), exist_ok=True)
    plt.savefig(op.join(output, interpolate_saveloc))


def extract_and_denoise_timeseries(
    func_filename,
    atlas_filename,
    verbose=2,
    interpolate=False,
    low_pass=None,
    t_r=None,
    output=None,
    **kwargs,
):
    if not len(func_filename):
        return [], []

    logging.info(f"Extracting and denoising timeseries for {len(func_filename)} files.")
    logging.debug(f"Denoising parameters are: {kwargs}")

    # There is currently a bug in nilearn that prevent "load_confounds" from finding
    # the confounds file if it contains any other BIDS entity than "ses" and "run".
    # It should be fixed in release 0.13.
    try:
        confounds, sample_mask = load_confounds(
            func_filename,
            demean=False,
            strategy=DENOISING_STRATEGY,
            motion="basic",
            **kwargs,
        )
    except ValueError:
        logging.warning(
            "Nilearn could not find the confounds file (this is likely due to a"
            " bug in nilearn.interface.fmriprep.load_confouds that should be fixed in"
            " release 0.13, see nilearn issue #3792)."
        )
        logging.warning("Searching manually ...")

        confounds, sample_mask = get_confounds_manually(
            func_filename,
            demean=False,
            strategy=DENOISING_STRATEGY,
            motion="basic",
            **kwargs,
        )

    # The outputs of "load_confounds" will not be in a list if
    # "func_filename" is a list with one element.
    if not isinstance(confounds, list):
        confounds = [confounds]
    if not isinstance(sample_mask, list):
        sample_mask = [sample_mask]

    if interpolate:
        interpolate_and_denoise_timeseries(
            func_filename,
            atlas_filename,
            confounds,
            sample_mask,
            t_r=t_r,
            low_pass=low_pass,
            output=output,
            verbose=verbose,
        )

    time_series = fit_transform_patched(
        func_filename,
        atlas_filename,
        confounds,
        sample_mask,
        low_pass=low_pass,
        t_r=t_r,
        standardize="zscore_sample",
        verbose=verbose,
        reports=True,
        n_jobs=8,
    )

    return time_series, confounds


def get_fc_strategy(strategy="sparse inverse covariance"):
    connectivity_kind = "correlation"
    connectivity_label = "correlation"
    estimator = LedoitWolf(store_precision=False)

    if strategy in ["cor", "corr", "correlation"]:
        connectivity_kind = "correlation"
        connectivity_label = "correlation"
        estimator = LedoitWolf(store_precision=False)
    elif strategy in ["sparse", "sparse inverse covariance"]:
        connectivity_kind = "precision"
        connectivity_label = "sparseinversecovariance"
        estimator = GraphicalLassoCV(alphas=6, max_iter=1000)

        if strategy not in ["sparse", "sparse inverse covariance"]:
            connectivity_kind = "covariance"
            connectivity_label = "covariance"

    return estimator, connectivity_kind, connectivity_label


def compute_connectivity(
    time_series,
    estimator=LedoitWolf(store_precision=False),
    connectivity_kind="correlation",
):
    if not len(time_series):
        return []
    n_ts = len(time_series)
    n_area = time_series[0].shape[-1]
    logging.info(
        f"Computing functional connectivity matrices for {n_ts} timeseries ..."
    )

    connectivity_estimator = ConnectivityMeasure(
        cov_estimator=estimator,
        kind=connectivity_kind,
        vectorize=True,
        discard_diagonal=True,
    )
    connectivity_measures = connectivity_estimator.fit_transform(time_series)
    return vec_to_sym_matrix(connectivity_measures, diagonal=np.zeros((n_ts, n_area)))


def plot_timeseries_carpet(timeseries, labels=None, networks=None):
    n_timepoints, n_area = timeseries.shape

    if labels is None:
        labels = np.arange(n_area)

    networks_provided = networks is not None

    sorting_index = np.arange(n_area)

    fig = plt.figure(figsize=TS_FIGURE_SIZE)
    gs = fig.add_gridspec(
        1,
        2,
        wspace=0,
        width_ratios=[0 + 0.005 * networks_provided, 1 - 0.005 * networks_provided],
    )
    ax_net, ax_carpet = gs.subplots()

    if networks_provided:
        networks_sorted = networks.sort_values()
        sorting_index = networks_sorted.index
        net_dict = {net: i + 1 for i, net in enumerate(networks_sorted.unique())}
        net_plot = np.array([[net_dict[net] for net in networks_sorted]])

        net_cmap = get_cmap(NETWORK_CMAP, len(net_dict))
        ax_net.imshow(net_plot.T, cmap=NETWORK_CMAP, aspect="auto")

        legend_elements = [
            Line2D(
                [0],
                [0],
                marker="s",
                color="w",
                label=net,
                markerfacecolor=net_cmap(val - 1),
                markersize=15,
            )
            for net, val in net_dict.items()
        ]

        ax_carpet.legend(
            handles=legend_elements,
            ncol=len(net_dict),
            loc="upper left",
            bbox_to_anchor=(0, 1.04),
            fontsize=LABELSIZE,
        )

    image = ax_carpet.imshow(
        timeseries.T[sorting_index],
        cmap="binary_r",
        aspect="auto",
        interpolation="antialiased",
    )
    cbar = plt.colorbar(image, pad=0, aspect=40)
    cbar.ax.tick_params(labelsize=LABELSIZE)

    ax_net.set_yticks(np.arange(n_area))
    ax_net.set_yticklabels(labels)
    ax_net.tick_params(bottom=False, labelbottom=False, labelsize=LABELSIZE)
    ax_carpet.set_xlabel("time", fontsize=LABELSIZE)
    ax_carpet.tick_params(left=False, labelleft=False, labelsize=LABELSIZE)

    plt.subplots_adjust(right=1.11, left=0.151)


def plot_timeseries_signal(
    timeseries,
    labels=None,
    networks=None,
    vert_scale=5,
    margin_value=0.01,
    color="tab:blue",
    linewidth=4,
    ax=None,
):
    n_timepoints, n_area = timeseries.shape

    if labels is None:
        labels = np.arange(n_area)

    networks_provided = networks is not None
    sorting_index = np.arange(n_area)
    colors = [color] * n_area

    if ax is None:
        _, ax = plt.subplots(figsize=TS_FIGURE_SIZE)

    if networks_provided:
        networks_sorted = networks.sort_values()
        sorting_index = networks_sorted.index
        net_dict = {net: i + 1 for i, net in enumerate(networks_sorted.unique())}
        net_plot = np.array([[net_dict[net] for net in networks_sorted]])

        net_cmap = get_cmap(NETWORK_CMAP, len(net_dict))

        colors = [net_cmap(i - 1) for i in net_plot][0]

        legend_elements = [
            Line2D(
                [0],
                [0],
                marker="s",
                color="w",
                label=net,
                markerfacecolor=net_cmap(val - 1),
                markersize=15,
            )
            for net, val in net_dict.items()
        ]

        ax.legend(
            handles=legend_elements,
            ncol=len(net_dict),
            loc="upper left",
            bbox_to_anchor=(0, 1.04),
            fontsize=LABELSIZE,
        )

    x_plot = np.arange(n_timepoints)
    for i, (roi_signal, col) in enumerate(zip(timeseries.T[sorting_index], colors)):
        ax.plot(x_plot, i * vert_scale + roi_signal, color=col, linewidth=linewidth)

    ax.set_yticks(np.arange(n_area) * vert_scale)
    ax.set_yticklabels(labels, fontsize=LABELSIZE)
    ax.set_xlabel("time", fontsize=LABELSIZE)

    ax.grid(visible=True, axis="y")
    ax.margins(x=margin_value, y=margin_value)

    return ax


def visual_report_timeserie(timeseries, filename, output, confounds=None, **kwargs):
    # Plotting denoised and aggregated timeseries
    for plot_func, plot_desc in zip(
        [plot_timeseries_carpet, plot_timeseries_signal], ["carpetplot", "timeseries"]
    ):
        ts_saveloc = get_bids_savename(
            filename, patterns=FIGURE_PATTERN, desc=plot_desc, **FIGURE_FILLS
        )
        plot_func(timeseries, **kwargs)

        logging.debug("Saving timeseries visual report at:")
        logging.debug(f"\t{op.join(output, ts_saveloc)}")
        os.makedirs(op.join(output, op.dirname(ts_saveloc)), exist_ok=True)
        plt.savefig(op.join(output, ts_saveloc))

    # Plotting confounds as a design matrix
    if confounds is not None:
        conf_saveloc = get_bids_savename(
            filename, patterns=FIGURE_PATTERN, desc="designmatrix", **FIGURE_FILLS
        )

        _, ax = plt.subplots(figsize=TS_FIGURE_SIZE)
        plot_design_matrix(confounds, ax=ax)
        logging.debug("Saving confounds visual report at:")
        logging.debug(f"\t{op.join(output, conf_saveloc)}")

        plt.savefig(op.join(output, conf_saveloc))


def visual_report_fc(matrix, filename, output, labels=None, **kwargs):
    fc_saveloc = get_bids_savename(
        filename, patterns=FIGURE_PATTERN, desc="heatmap", **kwargs
    )
    _, ax = plt.subplots(figsize=FC_FIGURE_SIZE)

    plot_matrix(matrix, labels=list(labels), axes=ax, vmin=-1, vmax=1)
    ax.tick_params(labelsize=LABELSIZE)

    # Update the size of the colorbar labels
    cbar = ax.images[-1].colorbar
    cbar.ax.tick_params(labelsize=LABELSIZE)

    # Ensure the labels are within the figure
    plt.tight_layout()

    logging.debug("Saving functional connectivity matrices visual report at:")
    logging.debug(f"\t{op.join(output, fc_saveloc)}")

    plt.savefig(op.join(output, fc_saveloc))


def save_output(data_list, original_filenames, output=None, **kwargs):
    for data, filename in zip(data_list, original_filenames):
        path_to_save = get_bids_savename(filename, **kwargs)
        saveloc = op.join(output, path_to_save)
        logging.debug(f"Saving data of type {type(data)} to: {saveloc}")
        os.makedirs(op.dirname(saveloc), exist_ok=True)
        np.savetxt(saveloc, data, delimiter="\t")


def main():
    args = get_arguments()

    input_path = args.data_dir
    save = args.save
    output = args.output

    task_filter = args.task
    overwrite = args.overwrite

    # denoise_only = args.denoise_only
    atlas_dimension = args.atlas_dimension
    low_pass = args.low_pass
    fd_threshold = args.FD_thresh
    std_dvars_threshold = args.SDVARS_thresh
    scrub = args.n_scrub_frames
    fc_estimator = args.fc_estimator
    interpolate = args.no_censor

    verbosity_level = args.verbosity
    nilearn_verbose = verbosity_level - 1

    logging_level_map = {
        0: logging.WARN,
        1: logging.INFO,
        2: logging.INFO,
        3: logging.DEBUG,
    }

    logging.basicConfig(
        # filename='example.log',
        # format='%(asctime)s %(levelname)s:%(message)s',
        format="%(levelname)s: %(message)s",
        level=logging_level_map[min([verbosity_level, 3])],
    )

    logging.captureWarnings(True)

    func_filenames, t_r = get_func_filenames_bids(input_path, task_filter=task_filter)
    logging.info(f"Found {len(func_filenames)} functional file(s):")
    logging.info(
        "\t" + "\n\t".join([op.basename(filename) for filename in func_filenames])
    )

    atlas_data = get_atlas_data(dimension=atlas_dimension)
    atlas_filename = getattr(atlas_data, "maps")
    atlas_labels = getattr(atlas_data, "labels").loc[:, "difumo_names"]
    atlas_network = getattr(atlas_data, "labels").loc[:, NETWORK_MAPPING]

    if output is None:
        run_name = f"DiFuMo{atlas_dimension:d}"
        run_name += (low_pass is not None) * "-LP"
        run_name += (interpolate) * "-noCensoring"

        output = op.join(
            find_derivative(input_path), "functional_connectivity", run_name
        )
    logging.info(f"Output will be save as derivatives in:\n\t{output}")

    covar_estimator, fc_kind, fc_label = get_fc_strategy(fc_estimator)
    logging.info(f"'{fc_label}' has been selected as connectivity metric")

    # By default, the timeseries and FC of all filenames in input will be computed
    missing_ts = missing_output = func_filenames.copy()
    existing_timeseries = []
    if not overwrite:
        logging.debug("Looking for existing timeseries ...")
        missing_ts, existing_ts = check_existing_output(
            output,
            func_filenames,
            return_existing=True,
            patterns=TIMESERIES_PATTERN,
            **TIMESERIES_FILLS,
        )

        logging.info(f"{len(missing_ts)} files are missing timeseries.")
        logging.debug("Looking for existing fc matrices ...")
        missing_only_fc = check_existing_output(
            output, existing_ts, patterns=FC_PATTERN, meas=fc_label, **FC_FILLS
        )
        missing_output = missing_ts + missing_only_fc
        logging.info(f"{len(missing_output)} files are missing FC matrices.")
        existing_timeseries = load_timeseries(missing_only_fc, output)

    time_series, all_confounds = extract_and_denoise_timeseries(
        missing_ts,
        atlas_filename,
        verbose=nilearn_verbose,
        t_r=t_r,
        low_pass=low_pass,
        fd_threshold=fd_threshold,
        std_dvars_threshold=std_dvars_threshold,
        scrub=scrub,
        interpolate=interpolate,
        output=output,
    )

    # Saving aggregated/denoised timeseries and visual reports
    if save and len(time_series):
        logging.info("Saving denoised timeseries ...")
        os.makedirs(output, exist_ok=True)
        save_output(
            time_series,
            missing_ts,
            output,
            patterns=TIMESERIES_PATTERN,
            **TIMESERIES_FILLS,
        )

        for individual_time_serie, confounds, filename in zip(
            time_series, all_confounds, missing_ts
        ):
            visual_report_timeserie(
                individual_time_serie,
                filename=filename,
                output=output,
                confounds=confounds,
                labels=atlas_labels,
                networks=atlas_network,
            )
    else:
        # TESTING VISUAL REPORTS
        for individual_time_serie, filename in zip(
            time_series + existing_timeseries, missing_output
        ):
            visual_report_timeserie(
                individual_time_serie,
                filename=filename,
                output=output,
                labels=atlas_labels,
                networks=atlas_network,
            )

    logging.info("Saving connectivity matrices ...")
    fc_matrices = compute_connectivity(
        time_series + existing_timeseries,
        estimator=covar_estimator,
        connectivity_kind=fc_kind,
    )

    # Saving FC matrices and visual reports
    if save and len(fc_matrices):
        logging.info("Saving connectivity matrices ...")
        save_output(
            fc_matrices,
            missing_output,
            output,
            patterns=FC_PATTERN,
            meas=fc_label,
            **FC_FILLS,
        )

        for individual_matrix, filename in zip(fc_matrices, missing_output):
            visual_report_fc(
                individual_matrix,
                filename=filename,
                output=output,
                labels=atlas_labels,
                meas=fc_label,
                **FIGURE_FILLS,
            )

    logging.info(
        f"Computation is done for {len(missing_output)} files out of the "
        f"{len(func_filenames)} provided."
    )
    logging.info("Functional connectivity finished successfully !")


if __name__ == "__main__":
    main()
