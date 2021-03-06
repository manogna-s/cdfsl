import torch.nn as nn
import torch
import numpy as np
from models.model_utils import CosineClassifier
from models.losses import *

def conv3x3(in_planes, out_planes, stride=1):
    """3x3 convolution with padding"""
    return nn.Conv2d(in_planes, out_planes, kernel_size=3, stride=stride,
                     padding=1, bias=False)


def conv1x1(in_planes, out_planes, stride=1):
    """1x1 convolution"""
    return nn.Conv2d(in_planes, out_planes, kernel_size=1, stride=stride, bias=False)


class BasicBlock(nn.Module):
    expansion = 1

    def __init__(self, inplanes, planes, stride=1, downsample=None):
        super(BasicBlock, self).__init__()
        self.conv1 = conv3x3(inplanes, planes, stride)
        self.bn1 = nn.BatchNorm2d(planes)
        self.relu = nn.ReLU(inplace=True)
        self.conv2 = conv3x3(planes, planes)
        self.bn2 = nn.BatchNorm2d(planes)
        self.downsample = downsample
        self.stride = stride

    def forward(self, x):
        identity = x

        out = self.conv1(x)
        out = self.bn1(out)
        out = self.relu(out)

        out = self.conv2(out)
        out = self.bn2(out)

        if self.downsample is not None:
            identity = self.downsample(x)

        out += identity
        out = self.relu(out)

        return out


class ResNet(nn.Module):

    def __init__(self, block, layers, classifier=None, num_classes=64,
                 dropout=0.0, global_pool=True):
        super(ResNet, self).__init__()
        self.initial_pool = False
        inplanes = self.inplanes = 64
        self.conv1 = nn.Conv2d(3, self.inplanes, kernel_size=5, stride=2,
                               padding=1, bias=False)
        self.bn1 = nn.BatchNorm2d(self.inplanes)
        self.relu = nn.ReLU(inplace=True)
        self.maxpool = nn.MaxPool2d(kernel_size=3, stride=2, padding=1)
        self.layer1 = self._make_layer(block, inplanes, layers[0])
        self.layer2 = self._make_layer(block, inplanes * 2, layers[1], stride=2)
        self.layer3 = self._make_layer(block, inplanes * 4, layers[2], stride=2)
        self.layer4 = self._make_layer(block, inplanes * 8, layers[3], stride=2)
        self.avgpool = nn.AdaptiveAvgPool2d((1, 1))
        self.dropout = nn.Dropout(dropout)
        self.outplanes = 512
        self.num_classes = num_classes

        # handle classifier creation
        if num_classes is not None:
            if classifier == 'linear':
                self.cls_fn = nn.Linear(self.outplanes, num_classes)
            elif classifier == 'cosine':
                self.cls_fn = CosineClassifier(self.outplanes, num_classes)

        for m in self.modules():
            if isinstance(m, nn.Conv2d):
                nn.init.kaiming_normal_(m.weight, mode='fan_out', nonlinearity='relu')
            elif isinstance(m, nn.BatchNorm2d):
                nn.init.constant_(m.weight, 1)
                nn.init.constant_(m.bias, 0)

    def _make_layer(self, block, planes, blocks, stride=1):
        downsample = None
        if stride != 1 or self.inplanes != planes * block.expansion:
            downsample = nn.Sequential(
                conv1x1(self.inplanes, planes * block.expansion, stride),
                nn.BatchNorm2d(planes * block.expansion),
            )

        layers = []
        layers.append(block(self.inplanes, planes, stride, downsample))
        self.inplanes = planes * block.expansion
        for _ in range(1, blocks):
            layers.append(block(self.inplanes, planes))

        return nn.Sequential(*layers)

    def forward(self, x):
        x = self.embed(x)
        x = self.dropout(x)
        x = self.cls_fn(x)
        return x

    def embed(self, x, param_dict=None):
        x = self.conv1(x)
        x = self.bn1(x)
        # print(self.bn1.training, self.bn1.track_running_stats)
        x = self.relu(x)
        if self.initial_pool:
            x = self.maxpool(x)

        x = self.layer1(x)
        x = self.layer2(x)
        x = self.layer3(x)
        x = self.layer4(x)

        x = self.avgpool(x)
        return x.squeeze()

    def get_final_features(self, episode):
        support_images, support_labels = episode['support_images'], episode['support_labels']
        query_images, query_labels = episode['query_images'], episode['query_labels']

        #support features
        support_features = self.embed(support_images)

        #query features
        query_features = self.embed(query_images)

        features = []
        labels = []
        labels.append(np.zeros_like(support_labels.cpu().data.numpy()) + support_labels.cpu().data.numpy())
        features.append(support_features)

        labels.append(np.ones_like(query_labels.cpu().data.numpy())*2*self.num_classes+query_labels.cpu().data.numpy())
        features.append(query_features)

        #classifier weights
        if self.cls_fn!=None:
            labels.append(3*self.num_classes+np.arange(self.num_classes))
            features.append(self.cls_fn.weight.T)

        #Centroids
        labels.append(4*self.num_classes+np.arange(self.num_classes))
        protos = compute_prototypes(support_features, support_labels, self.num_classes)
        features.append(protos)

        labels = np.concatenate(labels)
        features = torch.vstack(features)
        features = torch.nn.functional.normalize(features, p=2, dim=-1, eps=1e-12)
        features = features.cpu().data.numpy()

        return features, labels

    def get_state_dict(self):
        """Outputs all the state elements"""
        return self.state_dict()

    def get_parameters(self):
        """Outputs all the parameters"""
        return [v for k, v in self.named_parameters()]


def resnet18(pretrained=False, pretrained_model_path=None, **kwargs):
    """
        Constructs a ResNet-18 model.
    """
    model = ResNet(BasicBlock, [2, 2, 2, 2], **kwargs)
    if pretrained:
        device = model.get_state_dict()[0].device
        ckpt_dict = torch.load(pretrained_model_path, map_location=device)
        model.load_parameters(ckpt_dict['state_dict'], strict=False)
        print('Loaded shared weights from {}'.format(pretrained_model_path))
    return model