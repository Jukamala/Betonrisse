import random
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D
import matplotlib.cm as cm
import numpy as np
import torch
from torch.autograd import Variable
from matplotlib.animation import FFMpegWriter
from torchvision.transforms import Resize

from data import Betondataset, BetonImg, plot_batch, Resize_3d, Rotate_flip_3d, mean_std, data_hist
from paths import *
# from cnn_3d import Net
from models import LegNet1, Net, Net_from_Seg


"""
Tools to analyze model and dataset behaviour
"""

# order of filter (by inspection)
# old
# ORDER = np.array([11, 6, 9, 22, 12, 28, 0, 18, 5, 17, 21, 23, 2, 26, 10, 4, 29,
#                   27, 7, 8, 24, 25, 20, 13, 14, 30, 16, 19, 1, 31, 3, 15])
# air
ORDER = np.array([31, 25, 17, 16, 24, 29, 23, 12,  8,  4, 26, 22,  0, 18,  5,  7, 21,
                  14, 10,  9, 15,  6,  1, 11,  2, 27, 20, 13, 30, 28,  3, 19])


class FilterVisualizer:
    """
    With fixed model parameters, optimize input (i.e. image) to maximize activation in one filter.
    based on code from https://github.com/fg91/visualizing-cnn-feature-maps/blob/master/filter_visualizer.ipynb
    """
    def __init__(self, net, size=12, upscaling_steps=12, upscaling_factor=1.2):
        self.size, self.upscaling_steps, self.upscaling_factor = size, upscaling_steps, upscaling_factor
        self.model = net
        for param in net.parameters():
            param.requires_grad = False

    def visualize(self, filter, lr=0.1, opt_steps=20, blur=None):
        sz = self.size
        img = torch.from_numpy(np.uint8(np.random.uniform(150, 180, (1, sz, sz, sz))) / 255)[None].to(torch.float32)  # generate random image

        for _ in range(self.upscaling_steps):  # scale the image up upscaling_steps times
            img_var = Variable(img, requires_grad=True)  # convert image to Variable that requires grad
            optimizer = torch.optim.Adam([img_var], lr=lr, weight_decay=1e-6)
            for n in range(opt_steps):  # optimize pixel values for opt_steps times
                optimizer.zero_grad()
                input = Resize_3d((100, 100, 100))(img_var).to(device)
                activations, _ = self.model(Resize_3d((100, 100, 100))(img_var).to(device), filter=True)
                loss = -activations[0, filter].mean()
                loss.backward()
                optimizer.step()
            img = img_var.data.cpu().numpy()[0]
            print(sz)
            sz = int(self.upscaling_factor * sz)  # calculate new image size
            img = Resize_3d((sz, sz, sz))(torch.from_numpy(img[None])) # scale image up
            # if blur is not None:
            #     img = cv2.blur(img, (blur, blur))  # blur image to reduce high frequency patterns
        return input.detach().cpu()


def visualize(net):
    """ Visualize first 8 filters using activation maximization """
    fig, ax = plt.subplots(8, figsize=(10, 8))
    FV = FilterVisualizer(net, size=15, upscaling_steps=12, upscaling_factor=1.2)
    for i, fil in enumerate(ORDER[:8]):
        img = FV.visualize(fil, blur=5)
        plot_batch(img, acc=0, ax=ax[i], title="")
        plt.pause(0.01)
    plt.show()


def kernels(net):
    """ Visualize kernels """
    nm_paras = dict(net.named_parameters())
    try:
        kernel = nm_paras["conv0.0.weight"].detach().cpu()
    except KeyError:
        kernel = nm_paras["first_conv.0.weight"].detach().cpu()

    order = np.argsort([f.std() for f in kernel])[::-1].copy()
    print(order)
    global ORDER
    ORDER = order
    kernel = kernel[ORDER]

    fig, ax = plt.subplots(kernel.shape[4], kernel.shape[0], figsize=(15, 4))
    mi = np.percentile(kernel, 2)
    ma = np.percentile(kernel, 98)
    for i in range(kernel.shape[4]):
        for j in range(kernel.shape[0]):
            ax[i, j].set_axis_off()
            ax[i, j].imshow(kernel[j, 0, :, :, i], cmap="RdBu_r", vmin=mi, vmax=ma)
    fig.tight_layout()

    fig, ax = plt.subplots(3, 2)
    cmap = cm.get_cmap("viridis")
    for i in range(3):
        for j, n in enumerate(["weight", "bias"]):
            try:
                data = np.array(nm_paras["fc%d.%s" % (i+1, n)].detach().cpu())
            except KeyError:
                data = np.array(nm_paras["fc%d.0.%s" % (i + 1, n)].detach().cpu())
            if len(data.shape) < 2:
                data = data.reshape(-1, 1)
            data = data[np.argsort(data.std(axis=1))[::-1], :]
            ax[i, j].hist(data.T, bins=25, stacked=True, color=cmap(np.log(np.linspace(np.exp(0), np.exp(1), data.T.shape[1]))))
            ax[i, j].set_title("layer %d - %s" % (i+1, n))
            ax[i, j].set_yscale("log")
    fig.tight_layout()
    plt.show()


def explain(batch, net, anim=False):
    """ show filter activations on a batch """
    if anim:
        skip = 1
        Writer = FFMpegWriter(fps=20 / skip)
        ks = range(0, batch.shape[-1] - 1, skip)
        iss = [0]
    else:
        ks = [50]
        iss = range(batch.shape[0])

    for i in iss:
        fig, ax = plt.subplots(4, 8, figsize=(15, 8))
        ax = ax.reshape(-1)
        if anim:
            Writer.setup(fig, "filter.mp4", dpi=100)

        b = batch[i:i + 1]
        with torch.no_grad():
            filter, output = net(b.to(device), filter=True)
            filter = filter.cpu()
            output = output.cpu().float().view(-1)
            print(output, torch.sigmoid(output))

        # Normalize
        filter = (filter - filter.min()) / (filter.max() - filter.min())
        order = np.argsort([f.std() for f in filter[0]])[::-1].copy()
        print(order)
        order = ORDER
        filter = filter[0, order, :, :]

        for k in ks:
            filt = Resize((100, 100))(filter[:, :, :, k])

            for j in range(min(32, filter.shape[1])):
                cax = ax[j]
                cax.set_axis_off()
                cax.imshow(b[0, 0, :, :, k], cmap="gray", alpha=0.1)
                mi = np.percentile(filt[j], 2)
                ma = np.percentile(filt[j], 98)
                cax.imshow(filt[j], cmap="RdYlGn_r", vmin=mi, vmax=ma, alpha=filt[j])
            fig.tight_layout()
            if anim:
                Writer.grab_frame()
            else:
                plt.show()
    if anim:
        Writer.finish()


def symmetry(batch, net):
    """ analyze effect of rotation on network output """
    rot = Rotate_flip_3d()
    results = np.zeros((48, batch.shape[0]))
    with torch.no_grad():
        for i in range(batch.shape[0]):
            b = batch[i:i + 1]
            for perm in range(48):
                results[perm, i] = net(rot(b, perm).to(device)).cpu().float().view(-1)

    fig, ax = plt.subplots(1, 2, figsize=(10, 8))
    fig.suptitle("Rotations on input")
    sig_results = np.array(torch.sigmoid(torch.from_numpy(results)))
    bins = np.linspace(results.min(), results.max(), 25)
    sig_bins = np.linspace(sig_results.min(), sig_results.max(), 25)
    for i in range(batch.shape[0]):
        c = plt.get_cmap("tab10")(i)
        ax[0].hist(sig_results[:, i], bins=sig_bins, alpha=0.05, lw=0, color=c)
        ax[0].hist(sig_results[:, i], bins=sig_bins, fill=False, ec=c)
        ax[1].hist(results[:, i], bins=bins, alpha=0.05, lw=0, color=c)
        ax[1].hist(results[:, i], bins=bins, fill=False, ec=c)
    fig.tight_layout()
    ax[0].set_title("probability")
    ax[1].set_title("net output")
    plt.show()


def scale(batch, net):
    """ analyze effect of scale on network output """
    results = np.zeros((50, batch.shape[0]))
    scales = np.exp(np.linspace(np.log(1e-3), np.log(1e3)))
    with torch.no_grad():
        for i in range(batch.shape[0]):
            b = batch[i:i + 1]
            for j, sc in enumerate(scales):
                results[j, i] = net((b * sc).to(device)).cpu().float().view(-1)

    fig, ax = plt.subplots(1, 2, figsize=(10, 8))
    fig.suptitle("Scaling on input")
    sig_results = np.array(torch.sigmoid(torch.from_numpy(results)))
    ax[0].plot(scales, sig_results)
    ax[1].plot(scales, results)
    ax[0].set_xscale('log')
    ax[1].set_xscale('log')
    ax[0].set_title("probability")
    ax[1].set_title("net output")
    fig.tight_layout()
    plt.show()


def shift(batch, net, logx=True, scale=1):
    """ analyze effect of shift on network output """
    results = np.zeros((51, batch.shape[0]))
    if logx:
        shifts = np.exp(np.linspace(np.log(1e-2), np.log(1e2), 25))
    else:
        shifts = np.linspace(-1, 1, 51)
    with torch.no_grad():
        for i in range(batch.shape[0]):
            b = batch[i:i + 1]
            if logx:
                for j, sh in enumerate(shifts[::-1]):
                    results[j, i] = net((b - sh/scale).to(device)).cpu().float().view(-1)
                results[25, i] = net((b - sh).to(device)).cpu().float().view(-1)
                for j, sh in enumerate(shifts, 25):
                    results[j, i] = net((b + sh/scale).to(device)).cpu().float().view(-1)
            else:
                for j, sh in enumerate(shifts):
                    results[j, i] = net((b + sh/scale).to(device)).cpu().float().view(-1)

    if logx:
        fig, ax = plt.subplots(2, 2, figsize=(10, 8))
    else:
        fig, ax = plt.subplots(2, 1, figsize=(10, 8))

    fig.suptitle("Shifts on input")
    ax = ax.reshape(-1)
    sig_results = np.array(torch.sigmoid(torch.from_numpy(results)))
    if logx:
        ax[0].plot(shifts, sig_results[:25][::-1])
        ax[2].plot(shifts, results[:25][::-1])
        ax[0].set_xscale('log')
        ax[2].set_xscale('log')
        ax[0].invert_xaxis()
        ax[2].invert_xaxis()

        ax[1].plot(shifts, sig_results[26:])
        ax[3].plot(shifts, results[26:])
        ax[1].set_xscale('log')
        ax[3].set_xscale('log')
        ax[2].set_title("negative shift")
        ax[2].set_title("positive shift")
        ax[0].set_ylabel("probability")
        ax[2].set_ylabel("net output")
    else:
        ax[0].plot(shifts, sig_results)
        ax[1].plot(shifts, results)
    fig.tight_layout()
    plt.show()


def shift_and_scale(batch, net, labels=None):
    """ analyze effect of scale and shift on network output """
    results = np.zeros((4, 50, 51))
    scales = np.exp(np.linspace(np.log(1e-3), np.log(1e3), 50))
    shifts = np.linspace(-1, 1, 51)

    with torch.no_grad():
        for i in range(batch.shape[0]):
            if i > 4:
                continue
            b = batch[i:i + 1]
            for j, sc in enumerate(scales):
                print("[%.2f %%]" % (100 / 4 * (j / 50 + i)))
                for k, sh in enumerate(shifts):
                    results[i, j, k] = net(((b - sh) / sc).to(device)).cpu().float().view(-1)

    fig = plt.figure(figsize=(12, 8))
    fig1, fig23 = fig.subfigures(2, 1, height_ratios=[1, 2], hspace=0.2)
    fig2, fig3 = fig23.subfigures(1, 2, wspace=0.2)
    ax1 = fig1.subplots()
    ax2 = np.reshape(fig2.subplots(2, 2, subplot_kw={'projection': '3d'}), -1)
    ax3 = np.reshape(fig3.subplots(2, 2, subplot_kw={'projection': '3d'}), -1)
    fig1.suptitle("batch")
    fig2.suptitle("probability")
    if labels is not None:
        fig3.suptitle("- loss")
        pws = [2/3, 1, 1.5, 10]
        criterion = [torch.nn.BCEWithLogitsLoss(pos_weight=torch.tensor([pw]).float()) for pw in pws]
        losses = np.zeros((4, 50, 51))
        for i in range(4):
            for j in range(50):
                for k in range(51):
                    losses[i, j, k] = criterion[i](torch.tensor(results[:, j, k]).float(), torch.tensor(labels).float())
            min_idx = losses[i].reshape(-1).argmin()
            minpos = np.column_stack(np.unravel_index(min_idx, losses[i].shape)).reshape(-1)
            ax3[i].set_title("pw = %.1f [%4g, %4g]" % (pws[i], scales[minpos[0]], shifts[minpos[1]]))

    else:
        fig3.suptitle("net output")

    plot_batch(batch, ax=ax1, title="")

    X, Y = np.meshgrid(shifts, scales)
    for i in range(4):
        res = results[i]
        sig_res = np.array(torch.sigmoid(torch.from_numpy(res)))
        ax2[i].plot_surface(X, np.log10(Y), sig_res, cmap="viridis")
        ax2[i].yaxis.set_ticklabels(10 ** ax2[i].yaxis.get_ticklocs())
        ax2[i].set_xlabel('shift')
        ax2[i].set_ylabel('scale')
        ax2[i].view_init(elev=70, azim=75)

        if labels is not None:
            ax3[i].plot_surface(X, np.log10(Y), -np.clip(np.log10(losses[i]), a_min=-5, a_max=5), cmap="viridis")
            ax3[i].zaxis.set_ticklabels(10 ** ax3[i].zaxis.get_ticklocs())
        else:
            ax3[i].plot_surface(X, np.log10(Y), res, cmap="viridis")
        ax3[i].yaxis.set_ticklabels(10 ** ax3[i].yaxis.get_ticklocs())
        ax3[i].set_xlabel('shift')
        ax3[i].set_ylabel('scale')
        ax3[i].view_init(elev=70, azim=75)

    plt.show()


def double_hist(data1, data2):
    fig, ax = plt.subplots(2, 1, figsize=(12, 8))
    data_hist(data1, ax=ax[0], mult=255/9*1.2)
    data_hist(data2, ax=ax[1])
    plt.show()


if __name__ == "__main__":
    # Seed
    seed = 3
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)

    # Device
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

    synth = Betondataset("semisynth-inf", batch_size=4, shuffle=False, test=0)

    # BigImg
    path, idxs, bits, val_shift, val_scale = [
        TUBE_PATH, [(13, 3, 14), (8, 20, 14), (6, 6, 0), (10, 5, 0)], 8, 0.15, 5]  # 0.6, 2.7]
        # HPC_16_PATH, [(2, 3, 0), (1, 8, 0), (2, 9, 5), (4, 4, 4)], 16, 0.10, 10.92]  # 0.13, 0.55]
    val_set = BetonImg(path, max_val=2**bits, n=100, overlap=25)
    val = val_set.dataloader(batch_size=4, idxs=idxs, shuffle=False)

    # Dataset
    bits2, val_shift2, val_scale2 = 8, 0.082, 1.33
    val2 = Betondataset("real-val", batch_size=4, shuffle=False, test=0, norm=(0, 2**bits))

    # Normalization on training
    train_shift = 0
    train_scale = 1

    # Net
    # net, load = Net(layers=1, dropout=0.1, kernel_size=5).to(device), "checkpoints/air_big_ker_epoch_5"
    # net = LegNet1(layers=1).to(device)
    net, load = Net_from_Seg(layers=3), "checkpoints/unet_tin.cp"
    net.load_state_dict(torch.load(load, map_location=device))
    net.eval()

    # double_hist(val_set, val2.dataset)

    nxt_synth = next(iter(synth))["X"]
    # nxt_synth_norm = (nxt_synth - train_shift) / train_scale
    nxt_val = next(iter(val))["X"]
    # nxt_val_norm = (nxt_val - val_shift) / val_scale
    # plot_batch(nxt_synth)
    # plot_batch(nxt_val)
    # plt.show()

    # Synth: mean: 0.11 \ std: 0.03 --on train--> (0, 0.03)
    # Tube: mean: 96.71 \ std: 69.86 -> (0.38, 0.27) -> (0.04, 0.03)         -> mean_shift = 0.04,  scale = 9
    # HPC-16: mean: 6767.13 \ std: 976.87 -> (0.1, 0.02) -> (0.067, 0.03)    -> mean_shift = 0.067, scale = 0.67
    # Shai's real: mean: 27.59 \ std: 6.36 -> (0.11, 0.025) -> (0.130, 0.03) -> mean_shift = 0.13,  scale = 1.2

    # mean_std(synth.dataset, workers=2, batch_size=8)
    # mean_std(val_set.slices, workers=0, batch_size=1)
    # mean_std(val.dataset, workers=2, batch_size=8)

    # scale(nxt_synth - train_shift, net)
    # shift(nxt_synth / train_scale, net, scale=train_scale)
    # shift(nxt_synth / train_scale, net, logx=False, scale=train_scale)
    # shift_and_scale(nxt_synth, net, labels=[1, 1, 1, 0])
    # scale(nxt_val - val_shift, net)
    # shift(nxt_val / val_scale, net, scale=val_scale)
    # shift(nxt_val / val_scale, net, logx=False, scale=val_scale)
    # shift_and_scale(nxt_val, net, labels=[1, 0, 1, 1])

    # explain(nxt_synth, net, anim=False)
    # explain(nxt_val, net, anim=True)
    kernels(net)
    visualize(net)
    # symmetry(nxt_synth_norm, net)
    # symmetry(nxt_val * 255, net)
    # symmetry(nxt_val_norm, net)
    print("Done")
