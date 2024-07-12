from attention.modules import CBAM
from torch import nn
import torch
from backbone.resnet import CustomResNet, BasicBlock
from torch.nn import functional as F
from backbone.res2net import custom_res2net50_v1b


class SkipCBAMConnection(nn.Module):
    def __init__(self, f1_dim, f2_dim) -> None:
        super().__init__()

        self.cbam_1 = CBAM(f1_dim)
        self.cbam_2 = CBAM(f2_dim)
        self.dim = f1_dim

        self.conv = nn.Conv2d(f1_dim + f2_dim, f1_dim, 1)

    def forward(self, f1, f2):
        x1 = self.cbam_1(f1)
        x2 = self.cbam_2(f2)

        x = self.conv(torch.cat((x1, x2), dim=1))
        return x


class Encoder(nn.Module):
    def __init__(self) -> None:
        super().__init__()

        self.encoder = custom_res2net50_v1b()
        self.skip_1 = SkipCBAMConnection(64, 64)
        self.skip_2 = SkipCBAMConnection(128, 128)
        self.skip_3 = SkipCBAMConnection(256, 256)
        self.skip_4 = SkipCBAMConnection(512, 512)

    def forward(self, img_1, img_2):
        features_1 = self.encoder(img_1)
        features_2 = self.encoder(img_2)

        x1, x2, x3, x4 = features_1
        y1, y2, y3, y4 = features_2

        con_1 = self.skip_1(x1, y1)
        con_2 = self.skip_2(x2, y2)
        con_3 = self.skip_3(x3, y3)
        con_4 = self.skip_4(x4, y4)
        return con_1, con_2, con_3, con_4


class Decoder(nn.Module):
    def __init__(self) -> None:
        super().__init__()

        self.decoder_1 = BasicBlock(64, 1, 1)
        self.decoder_2 = BasicBlock(128, 64, 1)
        self.decoder_3 = BasicBlock(256, 128, 1)
        self.decoder_4 = BasicBlock(512, 256, 1)
        self.up = nn.Upsample(scale_factor=2, mode="bilinear")
        self.output = nn.Upsample(scale_factor=4, mode="bilinear")

        # self.dec_4 = nn.Conv2d(in_channels=512, out_channels=256, kernel_size=1)
        # self.dec_3 = nn.Conv2d(in_channels=256, out_channels=128, kernel_size=1)
        # self.dec_2 = nn.Conv2d(in_channels=128, out_channels=64, kernel_size=1)
        # self.dec_1 = nn.Conv2d(in_channels=64, out_channels=1, kernel_size=1)

    def forward(self, x):
        x1, x2, x3, x4 = x
        # x1 = x1.permute(0, 2, 3, 1)
        # x2 = x2.permute(0, 2, 3, 1)
        # x3 = x3.permute(0, 2, 3, 1)
        # x4 = x4.permute(0, 2, 3, 1)

        y4 = self.decoder_4(x4)
        # y4 = self.dec_4(y4)
        y4 = self.up(y4)

        y3 = x3 + y4
        y3 = self.decoder_3(x3)
        # y3 = self.dec_3(y3)
        y3 = self.up(y3)

        y2 = x2 + y3
        y2 = self.decoder_2(x2)
        # y2 = self.dec_2(y2)
        y2 = self.up(y2)

        y1 = x1 + y2
        y1 = self.decoder_1(x1)
        # out = self.dec_1(y1)
        out = y1
        # out = self.output(out)

        return out


class MLP(nn.Module):
    def __init__(self, dim, embed_dim):
        super().__init__()
        self.proj = nn.Linear(dim, embed_dim)

    def forward(self, x):
        x = x.flatten(2).transpose(1, 2)
        x = self.proj(x)
        return x


class ConvModule(nn.Module):
    def __init__(self, c1, c2):
        super().__init__()
        self.conv = nn.Conv2d(c1, c2, 1, bias=False)
        self.bn = nn.BatchNorm2d(c2)  # use SyncBN in original
        self.activate = nn.ReLU(True)

    def forward(self, x):
        return self.activate(self.bn(self.conv(x)))


class FusionModel(nn.Module):
    def __init__(self) -> None:
        super().__init__()
        self.encoder = Encoder()
        self.embed_dim = 256
        for i, dim in enumerate([64, 128, 256, 512]):
            self.add_module(f"linear_c{i+1}", ConvModule(dim, self.embed_dim))

        self.linear_fuse = ConvModule(self.embed_dim * 4, self.embed_dim)
        self.linear_pred = nn.Conv2d(self.embed_dim, 1, 1)
        self.dropout = nn.Dropout2d(0.1)
        self.sigmoid = nn.Sigmoid()
        # self.decoder = Decoder()

        # self.conv = nn.Conv2d(1, 3, 1)
        # self.bn1 = nn.BatchNorm2d(3)

    def forward(self, x, y):
        features = self.encoder(x, y)
        B, _, H, W = features[0].shape

        outs = [
            self.linear_c1(features[0])
            # .permute(0, 2, 1)
            # .reshape(B, -1, *features[0].shape[-2:])
        ]

        for i, feature in enumerate(features[1:]):
            cf = (
                eval(f"self.linear_c{i+2}")(feature)
                # .permute(0, 2, 1)
                # .reshape(B, -1, *feature.shape[-2:])
            )
            outs.append(
                F.interpolate(cf, size=(H, W), mode="bilinear", align_corners=False)
            )

        out = self.linear_fuse(torch.cat(outs[::-1], dim=1))
        out = self.linear_pred(self.dropout(out))
        out = self.sigmoid(out)

        return out


if __name__ == "__main__":
    model = FusionModel()
    x = torch.randn(1, 1, 256, 256)
    y = torch.randn(1, 1, 256, 256)
    out = model(x, y)
    print(out.shape)
